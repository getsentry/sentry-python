import json
from contextlib import nullcontext
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.aws_lambda.consts import ORIGIN
from sentry_sdk.traces import SENTRY_TRACE_HEADER_NAME
from sentry_sdk.tracing_utils import extract_sentrytrace_data
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, ContextManager, Optional

    from sentry_sdk._types import SpanLink

# `faas.trigger` of each `eventSource` of `Records` or of a top-level `eventSource`.
# https://docs.aws.amazon.com/lambda/latest/dg/lambda-services.html
_TRIGGERS: "dict[Optional[str], str]" = {
    "aws:sqs": "pubsub",
    "aws:sns": "pubsub",
    "aws:kinesis": "pubsub",
    "aws:ses": "pubsub",
    "aws:kafka": "pubsub",
    "SelfManagedKafka": "pubsub",
    "aws:mq": "pubsub",
    "aws:rmq": "pubsub",
    "aws:s3": "datasource",
    "aws:dynamodb": "datasource",
    "aws:docdb": "datasource",
}

# event sources that group their messages by topic-partition or queue.
# https://docs.aws.amazon.com/lambda/latest/dg/with-msk.html
# https://docs.aws.amazon.com/lambda/latest/dg/with-mq.html
_GROUPED_MESSAGES_KEYS: "dict[Optional[str], str]" = {
    "aws:kafka": "records",
    "SelfManagedKafka": "records",
    "aws:rmq": "rmqMessagesByQueue",
}

# `messaging.system` of the event sources whose producers can propagate a trace.
_MESSAGING_SYSTEMS = {"aws:sqs": "aws_sqs", "aws:sns": "aws.sns"}

# OTel's default limit; Lambda delivers up to 10,000 SQS messages at once.
# https://opentelemetry.io/docs/specs/otel/trace/sdk/#span-limits
MAX_PRODUCER_LINKS = 128

# https://docs.aws.amazon.com/eventbridge/latest/ref/overiew-event-structure.html
_EVENTBRIDGE_KEYS = frozenset(
    (
        "version",
        "id",
        "detail-type",
        "source",
        "account",
        "time",
        "region",
        "resources",
        "detail",
    )
)


def _get_records_source(records: "list[Any]") -> "Optional[str]":
    """The records' `eventSource`, one per event; SNS spells it `EventSource`."""
    return records[0].get("eventSource") or records[0].get("EventSource")


def _is_appsync_event(aws_event: "Any") -> bool:
    # https://docs.aws.amazon.com/appsync/latest/devguide/resolver-context-reference-js.html
    return (
        isinstance(aws_event, dict)
        and "arguments" in aws_event
        and "fieldName" in aws_event.get("info", {})
    )


def _get_trigger(aws_event: "Any") -> str:
    """`faas.trigger` from the envelope of the delivered event, never from its payload."""
    if isinstance(aws_event, list):
        # an AppSync batch resolver delivers a list of resolver events.
        if aws_event and all(_is_appsync_event(event) for event in aws_event):
            return "http"
        return "other"

    if not isinstance(aws_event, dict):
        return "other"

    # API Gateway REST APIs and ALB send `httpMethod`, HTTP APIs and function URLs
    # `requestContext.http`.
    request_context = aws_event.get("requestContext")
    if request_context is not None and (
        "httpMethod" in aws_event or "http" in request_context
    ):
        return "http"

    if _is_appsync_event(aws_event):
        return "http"

    records = aws_event.get("Records")
    if records:
        # CloudFront and Lambda@Edge records have no `eventSource`.
        if "cf" in records[0]:
            return "http"
        return _TRIGGERS.get(_get_records_source(records), "other")

    if _EVENTBRIDGE_KEYS <= aws_event.keys():
        if (
            aws_event["source"] == "aws.events"
            and aws_event["detail-type"] == "Scheduled Event"
        ):
            return "timer"
        return "pubsub"

    return _TRIGGERS.get(aws_event.get("eventSource"), "other")


def _get_messaging_batch_size(aws_event: "Any") -> "Optional[int]":
    """
    The number of messages delivered by a messaging event source, or `None` for any
    other event (HTTP, direct invocation, datasource, ...).
    """
    if not isinstance(aws_event, dict):
        return None

    records = aws_event.get("Records")
    if records:
        if _get_records_source(records) in ("aws:sqs", "aws:sns"):
            return len(records)
        return None

    event_source = aws_event.get("eventSource")
    if event_source == "aws:mq":
        return len(aws_event["messages"])

    grouped_messages_key = _GROUPED_MESSAGES_KEYS.get(event_source)
    if grouped_messages_key is None:
        return None

    return sum(len(messages) for messages in aws_event[grouped_messages_key].values())


def _get_producer_trace(record: "dict[str, Any]") -> "Optional[str]":
    """The `sentry-trace` the producer put in a record's message attributes."""
    # https://docs.aws.amazon.com/lambda/latest/dg/with-sns.html
    if record.get("EventSource") == "aws:sns":
        attribute = (
            record["Sns"].get("MessageAttributes", {}).get(SENTRY_TRACE_HEADER_NAME, {})
        )
        return attribute.get("Value")

    # https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html
    attribute = record.get("messageAttributes", {}).get(SENTRY_TRACE_HEADER_NAME)
    if attribute is not None:
        return attribute.get("stringValue")

    # without raw message delivery, an SNS subscription delivers the notification,
    # with its message attributes, as the body.
    # https://docs.aws.amazon.com/sns/latest/dg/sns-sqs-as-subscriber.html
    if '"TopicArn"' in record["body"]:
        notification = json.loads(record["body"])
        attribute = notification.get("MessageAttributes", {}).get(
            SENTRY_TRACE_HEADER_NAME, {}
        )
        return attribute.get("Value")

    return None


def _get_producer_links(records: "list[Any]") -> "list[SpanLink]":
    """One link per distinct producer context; messages never parent the invocation."""
    links: "list[SpanLink]" = []
    linked = set()
    for record in records:
        with capture_internal_exceptions():
            producer = extract_sentrytrace_data(_get_producer_trace(record))
            if producer is None:
                continue

            trace_id = str(producer["trace_id"])
            span_id = str(producer["parent_span_id"])
            if (trace_id, span_id) in linked:
                continue
            linked.add((trace_id, span_id))

            link: "SpanLink" = {"trace_id": trace_id, "span_id": span_id}
            if producer["parent_sampled"] is not None:
                link["sampled"] = bool(producer["parent_sampled"])
            links.append(link)
            if len(links) == MAX_PRODUCER_LINKS:
                break

    return links


def _start_process_span(aws_event: "Any") -> "ContextManager[Any]":
    """A `queue.process` span around the handler for an SQS or SNS delivery."""
    records = aws_event.get("Records") if isinstance(aws_event, dict) else None
    if not records:
        return nullcontext()

    event_source = _get_records_source(records)
    if event_source not in _MESSAGING_SYSTEMS:
        return nullcontext()

    # one queue or topic per event: `arn:aws:sqs:<region>:<account>:<queue>`.
    arn = (
        records[0]["eventSourceARN"]
        if event_source == "aws:sqs"
        else records[0]["Sns"]["TopicArn"]
    )
    destination = arn.split(":")[5]

    return sentry_sdk.start_span(
        name=f"process {destination}",
        attributes={
            SPANDATA.SENTRY_OP: OP.QUEUE_PROCESS,
            SPANDATA.SENTRY_ORIGIN: ORIGIN,
            SPANDATA.SENTRY_KIND: "consumer",
            SPANDATA.MESSAGING_SYSTEM: _MESSAGING_SYSTEMS[event_source],
            SPANDATA.MESSAGING_OPERATION_TYPE: "process",
            SPANDATA.MESSAGING_OPERATION_NAME: "process",
            SPANDATA.MESSAGING_DESTINATION_NAME: destination,
            SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT: len(records),
            SPANDATA.FAAS_TRIGGER: "pubsub",
        },
        links=_get_producer_links(records),
    )

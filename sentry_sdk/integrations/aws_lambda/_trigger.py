from contextlib import nullcontext
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.aws_lambda.consts import ORIGIN

if TYPE_CHECKING:
    from typing import Any, ContextManager

# `faas.trigger` of each `eventSource` of `Records` or of a top-level `eventSource`.
# https://docs.aws.amazon.com/lambda/latest/dg/lambda-services.html
_TRIGGERS = {
    "aws:sqs": "pubsub",
    "aws:sns": "pubsub",
    "aws:kinesis": "pubsub",
    "aws:ses": "pubsub",
    "aws:kafka": "pubsub",
    "SelfManagedKafka": "pubsub",
    # ActiveMQ: the Lambda docs show `aws:mq`, real deliveries use `aws:amq`.
    "aws:mq": "pubsub",
    "aws:amq": "pubsub",
    "aws:rmq": "pubsub",
    "aws:s3": "datasource",
    "aws:dynamodb": "datasource",
    "aws:docdb": "datasource",
}

# `messaging.system` of the messaging event sources that get a `queue.process` span.
_MESSAGING_SYSTEMS = {"aws:sqs": "aws_sqs", "aws:sns": "aws.sns"}

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
        # SNS spells it `EventSource`.
        source = records[0].get("eventSource") or records[0].get("EventSource")
        return _TRIGGERS.get(source, "other")

    if _EVENTBRIDGE_KEYS <= aws_event.keys():
        if (
            aws_event["source"] == "aws.events"
            and aws_event["detail-type"] == "Scheduled Event"
        ):
            return "timer"
        return "pubsub"

    return _TRIGGERS.get(aws_event.get("eventSource", ""), "other")


def _start_process_span(aws_event: "Any") -> "ContextManager[Any]":
    """A `queue.process` span around the handler for an SQS or SNS delivery."""
    records = aws_event.get("Records") if isinstance(aws_event, dict) else None
    if not records:
        return nullcontext()

    # SNS spells it `EventSource`.
    event_source = records[0].get("eventSource") or records[0].get("EventSource")
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
        },
    )

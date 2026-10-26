from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

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
    """The `eventSource` shared by all records; SNS spells it `EventSource`."""
    sources = {
        record.get("eventSource") or record.get("EventSource")
        if isinstance(record, dict)
        else None
        for record in records
    }
    return sources.pop() if len(sources) == 1 else None


def _is_appsync_event(aws_event: "Any") -> bool:
    # https://docs.aws.amazon.com/appsync/latest/devguide/resolver-context-reference-js.html
    return (
        isinstance(aws_event, dict)
        and "arguments" in aws_event
        and isinstance(aws_event.get("info"), dict)
        and "fieldName" in aws_event["info"]
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
    if isinstance(request_context, dict) and (
        "httpMethod" in aws_event or "http" in request_context
    ):
        return "http"

    if _is_appsync_event(aws_event):
        return "http"

    records = aws_event.get("Records")
    if isinstance(records, list) and records:
        # CloudFront and Lambda@Edge records have no `eventSource`.
        if isinstance(records[0], dict) and "cf" in records[0]:
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
    if isinstance(records, list):
        if records and _get_records_source(records) in ("aws:sqs", "aws:sns"):
            return len(records)
        return None

    event_source = aws_event.get("eventSource")
    if event_source == "aws:mq":
        return len(aws_event["messages"])

    grouped_messages_key = _GROUPED_MESSAGES_KEYS.get(event_source)
    if grouped_messages_key is None:
        return None

    return sum(len(messages) for messages in aws_event[grouped_messages_key].values())

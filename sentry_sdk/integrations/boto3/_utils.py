from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.tracing_utils import should_propagate_trace

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.traces import Span

    # tuple of (request param name, span attribute name).
    _AttributeSpec = Tuple[str, str]

# SQS and SNS reject a message with more than 10 message attributes.
# https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-message-metadata.html
_MAX_MESSAGE_ATTRIBUTES = 10

# body and attributes must fit the queue's or topic's configurable `MaximumMessageSize`;
# assume the 256 KiB default of SNS topics and older SQS queues; batch uses the same limit.
# https://docs.aws.amazon.com/sns/latest/api/API_PublishBatch.html
_MAX_MESSAGE_SIZE = 256 * 1024


def _extract_attributes(
    source: "Dict[str, Any]", specs: "Sequence[_AttributeSpec]"
) -> "Attributes":
    attributes = {}
    for param, attribute in specs:
        if param in source:
            attributes[attribute] = source[param]
    return attributes


def _get_aws_arn_attributes(
    values: "Iterable[Optional[str]]", services: "Sequence[str]"
) -> "Attributes":
    """Extract `cloud.account.id` and `cloud.resource_id` from the first matching ARN."""
    for value in values:
        if value is None or not value.startswith("arn:aws"):
            continue
        arn_parts = value.split(":", 5)
        if arn_parts[2] not in services:
            continue

        attributes: "Attributes" = {SPANDATA.CLOUD_RESOURCE_ID: value}
        if arn_parts[4]:
            attributes[SPANDATA.CLOUD_ACCOUNT_ID] = arn_parts[4]
        return attributes

    return {}


def _inject_message_attributes(
    ctx: "AwsCallContext", span: "Optional[Span]", body_key: str, entries_key: str
) -> "Optional[Dict[str, Any]]":
    """
    Return a copy of `ctx.params` with `sentry-trace` and `baggage` added in
    string message attributes, or to each batch entry with room for them.
    """
    if ctx.endpoint_url is None or not should_propagate_trace(
        sentry_sdk.get_client(), ctx.endpoint_url
    ):
        return None

    carrier = {
        name: {"DataType": "String", "StringValue": value}
        for name, value in sentry_sdk.get_current_scope().iter_trace_propagation_headers(
            span=span
        )
    }
    if not carrier:
        return None

    def _get_message_size(body: str, attributes: "Dict[str, Any]") -> int:
        size = len(body.encode())

        # names, types and values of message attributes count towards the message size.
        # https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-message-metadata.html
        for name, value in attributes.items():
            size += len(name.encode()) + len(value["DataType"].encode())
            if "StringValue" in value:
                size += len(value["StringValue"].encode())
            if "BinaryValue" in value:
                binary = value["BinaryValue"]
                size += len(binary.encode() if isinstance(binary, str) else binary)
        return size

    is_batch = entries_key in ctx.params
    messages = []
    for message in ctx.params[entries_key] if is_batch else (ctx.params,):
        attributes = message.get("MessageAttributes", {})
        injected = {**attributes, **carrier}
        if (
            # never replace the caller's carrier.
            not carrier.keys() & attributes.keys()
            # don't make AWS reject the message.
            and len(injected) <= _MAX_MESSAGE_ATTRIBUTES
            and _get_message_size(message[body_key], injected) <= _MAX_MESSAGE_SIZE
        ):
            message = {**message, "MessageAttributes": injected}
        messages.append(message)

    total_size = sum(
        _get_message_size(message[body_key], message.get("MessageAttributes", {}))
        for message in messages
    )
    if total_size > _MAX_MESSAGE_SIZE:
        return None

    return {**ctx.params, entries_key: messages} if is_batch else messages[0]

import json
import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import (
    _get_aws_arn_attributes,
    _inject_message_attributes,
)
from sentry_sdk.traces import BAGGAGE_HEADER_NAME, SENTRY_TRACE_HEADER_NAME, NoOpSpan
from sentry_sdk.tracing_utils import extract_sentrytrace_data

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional, Tuple

    from sentry_sdk._types import Attributes, SpanLink
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.traces import Span

# operation -> (`sentry.op`, `messaging.operation.type`)
_MESSAGING_OPERATIONS = {
    "SendMessage": (OP.QUEUE_PUBLISH, "send"),
    "SendMessageBatch": (OP.QUEUE_PUBLISH, "send"),
    "ReceiveMessage": (OP.QUEUE_RECEIVE, "receive"),
    "DeleteMessage": (OP.QUEUE_SETTLE, "settle"),
    "DeleteMessageBatch": (OP.QUEUE_SETTLE, "settle"),
}


def _message_link(message: "Dict[str, Any]") -> "Optional[SpanLink]":
    # message was sent straight to SQS, or through SNS with raw message delivery on.
    sentry_trace = (
        message.get("MessageAttributes", {})
        .get(SENTRY_TRACE_HEADER_NAME, {})
        .get("StringValue")
    )

    # SNS wraps the message and its attributes in a JSON envelope when raw message delivery is off.
    # https://docs.aws.amazon.com/sns/latest/dg/sns-large-payload-raw-message-delivery.html
    body = message.get("Body", "")
    if sentry_trace is None and f'"{SENTRY_TRACE_HEADER_NAME}"' in body:
        try:
            envelope = json.loads(body)
        except ValueError:
            envelope = None
        if isinstance(envelope, dict) and envelope.get("Type") == "Notification":
            sentry_trace = (
                envelope.get("MessageAttributes", {})
                .get(SENTRY_TRACE_HEADER_NAME, {})
                .get("Value")
            )

    data = extract_sentrytrace_data(sentry_trace)
    if data is None or not data["trace_id"] or not data["parent_span_id"]:
        return None

    link: "SpanLink" = {
        "trace_id": str(data["trace_id"]),
        "span_id": str(data["parent_span_id"]),
        # Sentry drops other link attributes for now; add `messaging.message.id` in the future.
        "attributes": {SPANDATA.SENTRY_LINK_TYPE: "message_origin"},
    }
    if data["parent_sampled"] is not None:
        link["sampled"] = bool(data["parent_sampled"])
    return link


class _SQSExtension(_ServiceExtension):
    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name in _MESSAGING_OPERATIONS:
            return _MESSAGING_OPERATIONS[ctx.operation_name][0]
        return None

    def get_span_name(
        self, ctx: "AwsCallContext", attributes: "Attributes"
    ) -> "Optional[str]":
        if ctx.operation_name not in _MESSAGING_OPERATIONS:
            return None
        # for example: `send <queue name>` when `messaging.operation.type=send`.
        # https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/#span-name
        return f"{attributes[SPANDATA.MESSAGING_OPERATION_TYPE]} {attributes[SPANDATA.MESSAGING_DESTINATION_NAME]}"

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes = _get_aws_arn_attributes(
            (ctx.params.get("DestinationArn"), ctx.params.get("SourceArn")), ("sqs",)
        )

        queue_url = ctx.params.get("QueueUrl")
        if queue_url is not None:
            attributes[SPANDATA.AWS_SQS_QUEUE_URL] = queue_url

            # `https://sqs.eu-north-1.amazonaws.com/123456789012/orders` => ['123456789012', 'orders']
            path = unquote(urlsplit(queue_url).path).strip("/").split("/")

            attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = path[-1]
            if re.fullmatch(r"[0-9]{12}", path[0]):
                attributes[SPANDATA.CLOUD_ACCOUNT_ID] = path[0]
        elif "QueueName" in ctx.params:
            # `CreateQueue` & `GetQueueUrl` operations.
            attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = ctx.params["QueueName"]

            if "QueueOwnerAWSAccountId" in ctx.params:
                attributes[SPANDATA.CLOUD_ACCOUNT_ID] = ctx.params[
                    "QueueOwnerAWSAccountId"
                ]
        elif "SourceArn" in ctx.params:
            # `arn:aws:sqs:eu-north-1:123456789012:orders` => `orders`
            attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = ctx.params[
                "SourceArn"
            ].split(":")[-1]

        if ctx.operation_name in _MESSAGING_OPERATIONS:
            operation_type = _MESSAGING_OPERATIONS[ctx.operation_name][1]
            attributes.update(
                {
                    SPANDATA.MESSAGING_SYSTEM: "aws_sqs",
                    SPANDATA.MESSAGING_OPERATION_TYPE: operation_type,
                    SPANDATA.MESSAGING_OPERATION_NAME: operation_type,
                }
            )
            if operation_type == "send":
                attributes[SPANDATA.SENTRY_KIND] = "producer"
            if "Entries" in ctx.params:
                attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] = len(
                    ctx.params["Entries"]
                )
        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        attributes: "Attributes" = {}

        # `ClientError` response holds no results.
        if "Error" in response:
            return attributes

        if ctx.operation_name == "SendMessage":
            attributes[SPANDATA.MESSAGING_MESSAGE_ID] = response["MessageId"]
        elif ctx.operation_name == "ReceiveMessage":
            # `Messages` is left out when no message was received.
            messages = response.get("Messages", ())
            attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] = len(messages)
            if len(messages) == 1:
                attributes[SPANDATA.MESSAGING_MESSAGE_ID] = messages[0]["MessageId"]

        # failed entries have a `200 OK` response; use their code when they share one.
        # https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_BatchResultErrorEntry.html
        error_codes = {entry["Code"] for entry in response.get("Failed", ())}
        if error_codes:
            attributes[SPANDATA.ERROR_TYPE] = (
                error_codes.pop()
                if len(error_codes) == 1
                else "aws_batch_partial_failure"
            )
        return attributes

    def get_response_links(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "List[SpanLink]":
        if ctx.operation_name != "ReceiveMessage":
            return []

        # one link per producer context; batch entries of one send share it.
        links: "Dict[Tuple[str, str], SpanLink]" = {}
        for message in response.get("Messages", ()):
            # returns a link to the producer context a received message carries, if valid.
            link = _message_link(message)
            if link is not None:
                links.setdefault((link["trace_id"], link["span_id"]), link)
        return list(links.values())

    def get_request_params(
        self, ctx: "AwsCallContext", span: "Optional[Span]"
    ) -> "Optional[Dict[str, Any]]":
        if ctx.operation_name in ("SendMessage", "SendMessageBatch"):
            return _inject_message_attributes(ctx, span, "MessageBody", "Entries")

        # only request the carrier when the receive span can record links.
        if (
            ctx.operation_name != "ReceiveMessage"
            or span is None
            or isinstance(span, NoOpSpan)
        ):
            return None

        # SQS only returns the message attributes named in `MessageAttributeNames`.
        # https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_ReceiveMessage.html
        names = ctx.params.get("MessageAttributeNames", [])
        missing = [
            name
            for name in (SENTRY_TRACE_HEADER_NAME, BAGGAGE_HEADER_NAME)
            if name not in names
        ]
        # `All` and `.*` already return every attribute, including ours.
        if not missing or "All" in names or ".*" in names:
            return None

        # append ours to the caller's names, in a copy of their params.
        return {**ctx.params, "MessageAttributeNames": [*names, *missing]}

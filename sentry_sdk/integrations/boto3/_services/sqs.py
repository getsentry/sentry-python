import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import _get_aws_arn_attributes

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext

# operation -> (`sentry.op`, `messaging.operation.type`)
_MESSAGING_OPERATIONS = {
    "SendMessage": (OP.QUEUE_PUBLISH, "send"),
    "SendMessageBatch": (OP.QUEUE_PUBLISH, "send"),
    "ReceiveMessage": (OP.QUEUE_RECEIVE, "receive"),
    "DeleteMessage": (OP.QUEUE_SETTLE, "settle"),
    "DeleteMessageBatch": (OP.QUEUE_SETTLE, "settle"),
}


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
        return f"{attributes[SPANDATA.MESSAGING_OPERATION_TYPE]} {attributes[SPANDATA.MESSAGING_DESTINATION_NAME]}"

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes = _get_aws_arn_attributes(
            (ctx.params.get("DestinationArn"), ctx.params.get("SourceArn")), ("sqs",)
        )

        queue_url = ctx.params.get("QueueUrl")
        if queue_url is not None:
            attributes[SPANDATA.AWS_SQS_QUEUE_URL] = queue_url
            # https://sqs.<region>.amazonaws.com/<account>/<queue name>
            path = unquote(urlsplit(queue_url).path).strip("/").split("/")
            attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = path[-1]
            if re.fullmatch(r"[0-9]{12}", path[0]):
                attributes[SPANDATA.CLOUD_ACCOUNT_ID] = path[0]
        elif "QueueName" in ctx.params:
            # `CreateQueue`, `GetQueueUrl`.
            attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = ctx.params["QueueName"]
            if "QueueOwnerAWSAccountId" in ctx.params:
                attributes[SPANDATA.CLOUD_ACCOUNT_ID] = ctx.params[
                    "QueueOwnerAWSAccountId"
                ]
        elif "SourceArn" in ctx.params:
            # message move tasks: arn:aws:sqs:<region>:<account>:<queue name>
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
        # a `ClientError` response holds no results.
        if "Error" in response:
            return attributes
        if ctx.operation_name == "SendMessage":
            attributes[SPANDATA.MESSAGING_MESSAGE_ID] = response["MessageId"]
        elif ctx.operation_name == "ReceiveMessage":
            # `Messages` is left out when no message was received.
            attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] = len(
                response.get("Messages", ())
            )
        # failed entries come back in a `200 OK` response.
        if response.get("Failed"):
            attributes[SPANDATA.ERROR_TYPE] = "aws_batch_partial_failure"
        return attributes

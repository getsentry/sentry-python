from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import _get_aws_arn_attributes

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext

_PUBLISH_OPERATIONS = ("Publish", "PublishBatch")


class _SNSExtension(_ServiceExtension):
    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name in _PUBLISH_OPERATIONS:
            return OP.QUEUE_PUBLISH
        # defaults to `rpc` for other operations.
        return None

    def get_span_name(
        self, ctx: "AwsCallContext", attributes: "Attributes"
    ) -> "Optional[str]":
        if ctx.operation_name not in _PUBLISH_OPERATIONS:
            return None

        destination = attributes.get(SPANDATA.MESSAGING_DESTINATION_NAME)
        if destination is None and SPANDATA.SERVER_ADDRESS in attributes:
            destination = f"{attributes[SPANDATA.SERVER_ADDRESS]}:{attributes[SPANDATA.SERVER_PORT]}"

        # for example: `send <topic name>` when `messaging.operation.type=send` (always send).
        # https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/#span-name
        return f"send {destination}" if destination else "send"

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {}

        # `TargetArn` can be either a topic or a platform endpoint; a topic ARN should
        # never contain ":endpoint/".
        target_arn = ctx.params.get("TargetArn")
        endpoint_arn = ctx.params.get("EndpointArn")
        if target_arn and ":endpoint/" in target_arn:
            endpoint_arn, target_arn = target_arn, None

        # `TopicArn` and `ResourceArn` are always topic ARNs.
        topic_arn = (
            ctx.params.get("TopicArn") or ctx.params.get("ResourceArn") or target_arn
        )

        if topic_arn:
            attributes[SPANDATA.AWS_SNS_TOPIC_ARN] = topic_arn
            # `arn:aws:sns:eu-north-1:123456789012:alerts` -> `alerts`
            attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = topic_arn.split(":")[-1]
        elif ctx.operation_name == "CreateTopic":
            attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = ctx.params["Name"]

        if ctx.operation_name in _PUBLISH_OPERATIONS:
            attributes.update(
                {
                    SPANDATA.SENTRY_KIND: "producer",
                    SPANDATA.MESSAGING_SYSTEM: "aws.sns",
                    SPANDATA.MESSAGING_OPERATION_TYPE: "send",
                    SPANDATA.MESSAGING_OPERATION_NAME: "publish",
                }
            )

            # phone numbers and device endpoints are PII.
            if "PhoneNumber" in ctx.params or endpoint_arn:
                attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = "[Filtered]"
            if ctx.operation_name == "PublishBatch":
                attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] = len(
                    ctx.params["PublishBatchRequestEntries"]
                )

        # endpoints and subscriptions identify a single device or subscriber.
        subscription_arn = ctx.params.get("SubscriptionArn")
        if not sentry_sdk.get_client().options["data_collection"]["user_info"]:
            if endpoint_arn:
                endpoint_arn = endpoint_arn.rsplit("/", 1)[0].replace(
                    ":endpoint/", ":app/", 1
                )
            if subscription_arn:
                subscription_arn = subscription_arn.rsplit(":", 1)[0]

        arns = (
            ctx.params.get("PlatformApplicationArn"),
            topic_arn,
            endpoint_arn,
            subscription_arn,
        )
        attributes.update(_get_aws_arn_attributes(arns, ("sns",)))
        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        attributes: "Attributes" = {}

        # `ClientError` response holds no results.
        if "Error" in response:
            return attributes

        if ctx.operation_name == "Publish":
            attributes[SPANDATA.MESSAGING_MESSAGE_ID] = response["MessageId"]

        # failed entries have a `200 OK` response; use their code when they share one.
        # https://docs.aws.amazon.com/sns/latest/api/API_BatchResultErrorEntry.html
        error_codes = {entry["Code"] for entry in response.get("Failed", ())}
        if len(error_codes) == 1:
            attributes[SPANDATA.ERROR_TYPE] = error_codes.pop()
        elif error_codes:
            attributes[SPANDATA.ERROR_TYPE] = "aws_batch_partial_failure"
        return attributes

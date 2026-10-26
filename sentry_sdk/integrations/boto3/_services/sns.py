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
        return None

    def get_span_name(
        self, ctx: "AwsCallContext", attributes: "Attributes"
    ) -> "Optional[str]":
        if ctx.operation_name not in _PUBLISH_OPERATIONS:
            return None
        destination = attributes.get(SPANDATA.MESSAGING_DESTINATION_NAME)
        if destination is None and SPANDATA.SERVER_ADDRESS in attributes:
            destination = f"{attributes[SPANDATA.SERVER_ADDRESS]}:{attributes[SPANDATA.SERVER_PORT]}"
        return f"send {destination}" if destination else "send"

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {}
        # `ResourceArn` (tagging, data protection policies) is always a topic.
        topic_arn = ctx.params.get("TopicArn") or ctx.params.get("ResourceArn")
        target_arn = ctx.params.get("TargetArn")
        # a `TargetArn` is a topic or a platform endpoint, i.e. a single device.
        endpoint_arn = target_arn if target_arn and ":endpoint/" in target_arn else None
        topic_arn = topic_arn or (target_arn if endpoint_arn is None else None)

        if topic_arn:
            attributes[SPANDATA.AWS_SNS_TOPIC_ARN] = topic_arn
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
            # phone numbers and device endpoints are personal destinations.
            if "PhoneNumber" in ctx.params or endpoint_arn:
                attributes[SPANDATA.MESSAGING_DESTINATION_NAME] = "[Filtered]"
            if ctx.operation_name == "PublishBatch":
                attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] = len(
                    ctx.params["PublishBatchRequestEntries"]
                )

        # endpoint and subscription ARNs identify a single device or subscriber.
        arns = [ctx.params.get("PlatformApplicationArn"), topic_arn]
        if sentry_sdk.get_client().options["data_collection"]["user_info"]:
            arns += [
                ctx.params.get("EndpointArn"),
                ctx.params.get("SubscriptionArn"),
                endpoint_arn,
            ]
        attributes.update(_get_aws_arn_attributes(arns, ("sns",)))
        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        attributes: "Attributes" = {}
        if ctx.operation_name == "Publish" and "MessageId" in response:
            attributes[SPANDATA.MESSAGING_MESSAGE_ID] = response["MessageId"]
        # failed entries come back in a `200 OK` response.
        if response.get("Failed"):
            attributes[SPANDATA.ERROR_TYPE] = "aws_batch_partial_failure"
        return attributes

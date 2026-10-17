from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import _get_aws_arn_attributes
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _SNSExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {}
        with capture_internal_exceptions():
            endpoint_arn = ctx.params.get("EndpointArn")
            subscription_arn = ctx.params.get("SubscriptionArn")
            target_arn = ctx.params.get("TargetArn")

            # platform endpoint and subscription ARNs identify a single device or
            # subscriber, so gate them with `user_info`; endpoint can be a device
            # token, target ARN can be an endpoint, and subscriber email/phone number.
            if not sentry_sdk.get_client().options["data_collection"]["user_info"]:
                endpoint_arn = None
                subscription_arn = None
                if target_arn and ":endpoint/" in target_arn:
                    target_arn = None

            attributes.update(
                _get_aws_arn_attributes(
                    (
                        endpoint_arn,
                        ctx.params.get("PlatformApplicationArn"),
                        subscription_arn,
                        target_arn,
                        ctx.params.get("TopicArn"),
                    ),
                    ("sns",),
                )
            )
        return attributes

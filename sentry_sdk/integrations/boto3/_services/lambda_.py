from typing import TYPE_CHECKING

from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import _get_aws_arn_attributes
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _LambdaExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {}
        with capture_internal_exceptions():
            attributes.update(
                _get_aws_arn_attributes(
                    (ctx.params.get("FunctionName"),),
                    ("lambda",),
                )
            )
        return attributes

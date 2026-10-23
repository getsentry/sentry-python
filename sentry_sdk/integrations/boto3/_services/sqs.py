import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import _get_aws_arn_attributes
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _SQSExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {}
        with capture_internal_exceptions():
            attributes.update(
                _get_aws_arn_attributes(
                    (
                        ctx.params.get("DestinationArn"),
                        ctx.params.get("SourceArn"),
                    ),
                    ("sqs",),
                )
            )

        queue_url = ctx.params.get("QueueUrl")
        if queue_url is None:
            return attributes

        with capture_internal_exceptions():
            account_id = unquote(urlsplit(queue_url).path).strip("/").split("/", 1)[0]
            if re.fullmatch(r"^[0-9]{12}$", account_id):
                attributes[SPANDATA.CLOUD_ACCOUNT_ID] = account_id
        return attributes

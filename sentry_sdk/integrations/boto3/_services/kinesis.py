from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import _get_aws_arn_attributes
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _KinesisExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {}

        # a stream name, or an arn that contains it: `…:stream/<name>` for a stream,
        # `…:stream/<name>/consumer/<consumer>:<timestamp>` for a consumer.
        for param in ("StreamName", "StreamARN", "ConsumerARN", "ResourceARN"):
            if param in ctx.params:
                attributes[SPANDATA.AWS_KINESIS_STREAM_NAME] = (
                    ctx.params[param].split(":stream/", 1)[-1].split("/", 1)[0]
                )
                break

        with capture_internal_exceptions():
            attributes.update(
                _get_aws_arn_attributes(
                    (
                        ctx.params.get("ChannelARN"),
                        ctx.params.get("ConsumerARN"),
                        ctx.params.get("ResourceARN"),
                        ctx.params.get("StreamARN"),
                    ),
                    ("kinesis",),
                )
            )

        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        # `PutRecords` returns `200 OK` even when records fail.
        # https://docs.aws.amazon.com/kinesis/latest/APIReference/API_PutRecords.html
        if ctx.operation_name != "PutRecords" or not response.get("FailedRecordCount"):
            return {}

        error_codes = {
            record["ErrorCode"]
            for record in response["Records"]
            if "ErrorCode" in record
        }
        return {
            SPANDATA.ERROR_TYPE: error_codes.pop()
            if len(error_codes) == 1
            else "aws_batch_partial_failure"
        }

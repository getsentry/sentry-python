import json
from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services._utils import (
    _extract_attributes,
)
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

if TYPE_CHECKING:
    from typing import Any, Sequence

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.integrations.boto3._services._utils import (
        _AttributeSpec,
    )

_RESPONSE_BODY_SIZE_OPERATIONS = frozenset(
    (
        "GetObject",
        "GetObjectAnnotation",
    )
)

_RESPONSE_FILE_SIZE_FIELDS = {
    "GetObjectAttributes": "ObjectSize",
    "PutObject": "Size",
}

_REQUEST_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    # s3-specific attributes defined by OTel SemConv.
    # https://opentelemetry.io/docs/specs/semconv/object-stores/s3/
    ("Bucket", SPANDATA.AWS_S3_BUCKET),
    ("Key", SPANDATA.AWS_S3_KEY),
    ("UploadId", SPANDATA.AWS_S3_UPLOAD_ID),
)


class _S3Extension(_ServiceExtension):
    __slots__ = ()

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = _extract_attributes(ctx.params, _REQUEST_ATTRIBUTES)

        if "CopySource" in ctx.params:
            # boto3 either "bucket/key" or a dictionary with `Bucket`, `Key`, and optional `VersionId` for `CopySource`.
            # https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/upload_part_copy.html
            copy_source = ctx.params["CopySource"]
            if isinstance(copy_source, str):
                attributes[SPANDATA.AWS_S3_COPY_SOURCE] = copy_source
            else:
                value = f"{copy_source['Bucket']}/{copy_source['Key']}"
                if "VersionId" in copy_source:
                    value += f"?versionId={copy_source['VersionId']}"
                attributes[SPANDATA.AWS_S3_COPY_SOURCE] = value

        # OTel defines `PartNumber` for `UploadPart` and `UploadPartCopy` only.
        # https://opentelemetry.io/docs/specs/semconv/object-stores/s3/#attributes
        if (
            ctx.operation_name in ("UploadPart", "UploadPartCopy")
            and "PartNumber" in ctx.params
        ):
            attributes[SPANDATA.AWS_S3_PART_NUMBER] = ctx.params["PartNumber"]

        if "Delete" in ctx.params:
            attributes[SPANDATA.AWS_S3_DELETE] = json.dumps(
                ctx.params["Delete"], separators=(",", ":"), sort_keys=True
            )

        if (
            ctx.operation_name == "CompleteMultipartUpload"
            and "MpuObjectSize" in ctx.params
        ):
            attributes[SPANDATA.FILE_SIZE] = ctx.params["MpuObjectSize"]

        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        attributes: "Attributes" = {}
        operation_name = ctx.operation_name

        if (
            operation_name in _RESPONSE_BODY_SIZE_OPERATIONS
            and "ContentLength" in response
        ):
            # `ContentLength` is the size of the HTTP body returned, which may be a range.
            attributes[SPANDATA.HTTP_BODY_SIZE] = response["ContentLength"]

        # report the complete file size, not just the HTTP body size.
        file_size_field = _RESPONSE_FILE_SIZE_FIELDS.get(operation_name)
        if file_size_field is not None and file_size_field in response:
            attributes[SPANDATA.FILE_SIZE] = response[file_size_field]

        if (
            operation_name == "HeadObject"
            and "Range" not in ctx.params
            and "PartNumber" not in ctx.params
            and "ContentLength" in response
        ):
            # an un-ranged `HEAD` has no body, so `ContentLength` is the file size.
            attributes[SPANDATA.FILE_SIZE] = response["ContentLength"]

        return attributes

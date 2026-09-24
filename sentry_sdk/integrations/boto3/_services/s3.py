import json
from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services._attribute_extraction import (
    _as_integer,
    _as_string,
    _extract_attributes,
)
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

if TYPE_CHECKING:
    from typing import Any, Optional, Sequence

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.integrations.boto3._services._attribute_extraction import (
        _AttributeSpec,
    )

_RESPONSE_BODY_SIZE_OPERATIONS = frozenset(
    (
        "GetObject",
        "GetObjectAnnotation",
    )
)

_RESPONSE_OBJECT_SIZE_FIELDS = {
    "GetObjectAttributes": "ObjectSize",
    "PutObject": "Size",
}


def _json_dict(value: "Any") -> "Optional[str]":
    if not isinstance(value, dict):
        return None

    try:
        return json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return None


_REQUEST_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    # s3-specific attributes defined by OTel SemConv. Specified as a tuple of
    # (param_name, attribute_name, converter_func). the `converter_func` is
    # used to 1. validate the value (otherwise omitted) and 2. convert it to
    # the appropriate type.
    # https://opentelemetry.io/docs/specs/semconv/object-stores/s3/
    ("Bucket", SPANDATA.AWS_S3_BUCKET, _as_string),
    ("CopySource", SPANDATA.AWS_S3_COPY_SOURCE, _as_string),
    ("Delete", SPANDATA.AWS_S3_DELETE, _json_dict),
    ("Key", SPANDATA.AWS_S3_KEY, _as_string),
    ("PartNumber", SPANDATA.AWS_S3_PART_NUMBER, _as_integer),
    ("UploadId", SPANDATA.AWS_S3_UPLOAD_ID, _as_string),
)


class _S3Extension(_ServiceExtension):
    __slots__ = ()

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = _extract_attributes(ctx.params, _REQUEST_ATTRIBUTES)

        if ctx.operation_name == "CompleteMultipartUpload":
            object_size = _as_integer(ctx.params.get("MpuObjectSize"))
            if object_size is not None and object_size >= 0:
                attributes[SPANDATA.AWS_S3_OBJECT_SIZE] = object_size

        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        if not isinstance(response, dict):
            return {}

        attributes: "Attributes" = {}
        operation_name = ctx.operation_name

        if operation_name in _RESPONSE_BODY_SIZE_OPERATIONS:
            # `ContentLength` is the size of the HTTP body returned, which may be a range.
            content_length = _as_integer(response.get("ContentLength"))
            if content_length is not None and content_length >= 0:
                attributes[SPANDATA.HTTP_RESPONSE_BODY_SIZE] = content_length

        # these fields report the total S3 object size, not the HTTP body size.
        object_size_field = _RESPONSE_OBJECT_SIZE_FIELDS.get(operation_name)
        if object_size_field is not None:
            object_size = _as_integer(response.get(object_size_field))
            if object_size is not None and object_size >= 0:
                attributes[SPANDATA.AWS_S3_OBJECT_SIZE] = object_size

        if (
            operation_name == "HeadObject"
            and "Range" not in ctx.params
            and "PartNumber" not in ctx.params
        ):
            # an un-ranged `HEAD` has no body, so `ContentLength` is the object size.
            object_size = _as_integer(response.get("ContentLength"))
            if object_size is not None and object_size >= 0:
                attributes[SPANDATA.AWS_S3_OBJECT_SIZE] = object_size

        return attributes

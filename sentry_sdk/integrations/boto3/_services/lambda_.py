import base64
import json
from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import (
    _extract_attributes,
    _get_aws_arn_attributes,
    _get_propagation_headers,
)
from sentry_sdk.integrations.boto3.consts import CLOUD_PROVIDER
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Sequence

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.integrations.boto3._utils import _AttributeSpec
    from sentry_sdk.traces import Span

# set on both request and response of the event source mapping apis.
_RESOURCE_MAPPING_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    ("UUID", SPANDATA.AWS_LAMBDA_RESOURCE_MAPPING_ID),
)


class _LambdaExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = _extract_attributes(
            ctx.params, _RESOURCE_MAPPING_ATTRIBUTES
        )
        if "FunctionName" not in ctx.params:
            return attributes

        # a name, a partial arn (`<account>:function:<name>`) or an arn, each optionally
        # followed by `:<version or alias>`.
        function_name = ctx.params["FunctionName"]
        name = function_name.split(":function:")[-1].split(":", 1)[0]
        invoke = ctx.operation_name in (
            "Invoke",
            "InvokeAsync",
            "InvokeWithResponseStream",
        )
        if invoke:
            attributes[SPANDATA.FAAS_INVOKED_NAME] = name
            attributes[SPANDATA.FAAS_INVOKED_PROVIDER] = CLOUD_PROVIDER
            if ctx.region_name is not None:
                attributes[SPANDATA.FAAS_INVOKED_REGION] = ctx.region_name
        else:
            attributes[SPANDATA.AWS_LAMBDA_TARGET_FUNCTION_NAME] = name

        # params aren't validated yet by botocore.
        with capture_internal_exceptions():
            arn_attributes = _get_aws_arn_attributes((function_name,), ("lambda",))
            attributes.update(arn_attributes)
            if invoke and arn_attributes:
                attributes[SPANDATA.FAAS_INVOKED_REGION] = function_name.split(":")[3]

        return attributes

    def get_request_params(
        self, ctx: "AwsCallContext", span: "Optional[Span]"
    ) -> "Optional[Dict[str, Any]]":
        # lambda only passes `ClientContext` to the function on synchronous invocations.
        # https://docs.aws.amazon.com/lambda/latest/api/API_Invoke.html#lambda-Invoke-request-ClientContext
        if (
            ctx.operation_name not in ("Invoke", "InvokeWithResponseStream")
            or ctx.params.get("InvocationType", "RequestResponse") != "RequestResponse"
        ):
            return None

        headers = _get_propagation_headers(ctx, span)
        client_context = (
            json.loads(base64.b64decode(ctx.params["ClientContext"]))
            if "ClientContext" in ctx.params
            else {}
        )
        custom = client_context.get("custom", {})
        # never mix with or replace trace data the caller propagates itself.
        if not headers or any(key in custom for key in headers):
            return None

        client_context["custom"] = {**custom, **headers}
        encoded = base64.b64encode(
            json.dumps(client_context, separators=(",", ":")).encode()
        ).decode()
        # it takes at most 3,583 bytes of base64.
        if len(encoded) > 3583:
            return None
        return {**ctx.params, "ClientContext": encoded}

    def has_streaming_response(self, ctx: "AwsCallContext") -> bool:
        # `Invoke` returns once the function finished; its `Payload` is the complete
        # result, not a stream. ending with the call keeps the span of a caller that
        # never reads it.
        return ctx.operation_name != "Invoke"

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        attributes = _extract_attributes(response, _RESOURCE_MAPPING_ATTRIBUTES)
        # `Invoke` returns `200 OK` when the function fails, with `FunctionError` set. the
        # payload holds the error and is never read.
        # https://docs.aws.amazon.com/lambda/latest/api/API_Invoke.html#lambda-Invoke-response-FunctionError
        if "FunctionError" in response:
            attributes[SPANDATA.ERROR_TYPE] = "lambda_function_error"
        return attributes

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3._context import AwsCallContext
from sentry_sdk.integrations.boto3._instrumentation import (
    _finish_span,
    _get_error_attributes,
    _get_response_attributes,
    _instrument_streaming_body,
    _sentry_before_sign,
    _sentry_request_created,
    _start_client_span,
)
from sentry_sdk.integrations.boto3._services.bedrock import _BedrockExtension
from sentry_sdk.integrations.boto3._services.dynamodb import _DynamoDBExtension
from sentry_sdk.integrations.boto3._services.kinesis import _KinesisExtension
from sentry_sdk.integrations.boto3._services.lambda_ import _LambdaExtension
from sentry_sdk.integrations.boto3._services.s3 import _S3Extension
from sentry_sdk.integrations.boto3._services.secretsmanager import (
    _SecretsManagerExtension,
)
from sentry_sdk.integrations.boto3._services.sns import _SNSExtension
from sentry_sdk.integrations.boto3._services.sqs import _SQSExtension
from sentry_sdk.integrations.boto3._services.stepfunctions import (
    _StepFunctionsExtension,
)
from sentry_sdk.integrations.boto3.consts import IDENTIFIER
from sentry_sdk.traces import NoOpSpan, Span
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Iterator, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3 import Boto3Integration
    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

try:
    from botocore.client import BaseClient
except ImportError:
    raise DidNotEnable("botocore not installed")


@contextmanager
def _activate_client_span(
    span: "Span",
) -> "Iterator[Span]":
    """
    Activate the client span temporarily during `_make_api_call()` without ending it.

    Botocore returns a `StreamingBody` before its bytes are consumed. Using the
    context manager would finish it as soon as `_make_api_call()` returns, so
    restore the caller's span here and let the `StreamingBody` wrapper finish
    the client span when the body is consumed or closed.

    faulty:                               desired:
           boto3  [_make_api_call]                boto3  [_make_api_call------]
           http     [request]                     http       [request]
           stream               [read]            stream                [read]
    """
    if isinstance(span, NoOpSpan):
        yield span
        return

    scope = sentry_sdk.get_current_scope()
    previous_span = scope.span
    scope.span = span
    try:
        yield span
    finally:
        scope.span = previous_span


def _patch_botocore_client() -> None:
    orig_init = BaseClient.__init__
    orig_make_api_call = BaseClient._make_api_call  # type: ignore

    def sentry_patched_init(self: "BaseClient", *args: "Any", **kwargs: "Any") -> None:
        orig_init(self, *args, **kwargs)
        with capture_internal_exceptions():
            self.meta.events.register("request-created", _sentry_request_created)
            # run after other `before-sign` handlers so existing baggage is preserved.
            self.meta.events.register_last("before-sign", _sentry_before_sign)

    def sentry_patched_make_api_call(
        self: "BaseClient", operation_name: str, api_params: "Dict[str, Any]"
    ) -> "Any":
        """
        Track a single API call, including retries, serialization, and endpoint
        resolution. For streaming responses, keep the span open until the
        response body is consumed or closed.
        https://github.com/boto/botocore/blob/358f8eec8c76201bb1a7a35644abcbc9036de7ed/botocore/client.py
        https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        """
        integration = cast(
            "Optional[Boto3Integration]",
            sentry_sdk.get_client().get_integration(IDENTIFIER),
        )
        if integration is None:
            return orig_make_api_call(self, operation_name, api_params)

        span: "Optional[Span]" = None
        with capture_internal_exceptions():
            ctx = AwsCallContext(operation_name, api_params)
            with capture_internal_exceptions():
                # add optional metadata to the context, e.g. service-name, region-name, etc.
                ctx.add_metadata(self)

            service_ext: "Optional[_ServiceExtension]" = None
            if ctx.service_name == "bedrock-runtime":
                service_ext = _BedrockExtension()
            elif ctx.service_name == "dynamodb":
                service_ext = _DynamoDBExtension()
            elif ctx.service_name == "kinesis":
                service_ext = _KinesisExtension()
            elif ctx.service_name == "lambda":
                service_ext = _LambdaExtension()
            elif ctx.service_name == "s3":
                service_ext = _S3Extension()
            elif ctx.service_name == "secretsmanager":
                service_ext = _SecretsManagerExtension()
            elif ctx.service_name == "sns":
                service_ext = _SNSExtension()
            elif ctx.service_name == "sqs":
                service_ext = _SQSExtension()
            elif ctx.service_name == "stepfunctions":
                service_ext = _StepFunctionsExtension()

            span = _start_client_span(ctx, service_ext)
            if service_ext is not None and integration.propagate_traces:
                # override the request params for e.g. trace propagation.
                api_params = service_ext.get_request_params(ctx, span) or api_params

        if span is None:
            return orig_make_api_call(self, operation_name, api_params)

        # activate without finishing; a streaming response may outlive the call.
        attributes: "Attributes" = {}
        try:
            if service_ext is not None:
                service_ext.request_start_time = time.perf_counter()
            with _activate_client_span(span):
                try:
                    parsed = orig_make_api_call(self, operation_name, api_params)
                except BaseException as error:
                    with capture_internal_exceptions():
                        attributes.update(_get_error_attributes(error))
                    with capture_internal_exceptions():
                        span.set_attributes(attributes)
                    raise
                with capture_internal_exceptions():
                    attributes.update(_get_response_attributes(parsed))
                with capture_internal_exceptions():
                    span.set_attributes(attributes)
        except BaseException as error:
            _finish_span(span, error)
            raise

        if service_ext is not None:
            service_ext.on_success_response(ctx, parsed, span)
            return parsed

        streaming_body_instrumented = False
        with capture_internal_exceptions():
            streaming_body_instrumented = _instrument_streaming_body(span, parsed)

        # `StreamingBody`s finish their span when consumed or closed.
        if not streaming_body_instrumented:
            span.end()
        return parsed

    BaseClient.__init__ = sentry_patched_init  # type: ignore
    BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore

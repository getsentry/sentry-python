from contextlib import contextmanager
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3._context import AwsCallContext
from sentry_sdk.integrations.boto3._instrumentation import (
    _finish_span,
    _instrument_streaming_body,
    _sentry_before_sign,
    _sentry_request_created,
    _start_client_span,
)
from sentry_sdk.traces import NoOpStreamedSpan, StreamedSpan
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Iterator, Optional, Union

    from sentry_sdk.tracing import Span

try:
    from botocore.client import BaseClient
except ImportError:
    raise DidNotEnable("botocore not installed")


@contextmanager
def _activate_client_span(
    span: "Union[Span, StreamedSpan]",
) -> "Iterator[Union[Span, StreamedSpan]]":
    """
    Activate the boto span temporarily during `_make_api_call()` without ending it.

    Botocore returns a `StreamingBody` before its bytes are consumed. Using the
    context manager would finish it as soon as `_make_api_call()` returns, so
    restore the caller's span here and let the `StreamingBody` wrapper finish
    the boto span when body is consumed/closed.

    faulty:                               desired:
           boto3  [_make_api_call]                boto3  [_make_api_call------]
           http     [request]                     http       [request]
           stream               [read]            stream                [read]
    """
    if isinstance(span, NoOpStreamedSpan):
        yield span
        return

    scope = sentry_sdk.get_current_scope()
    if not isinstance(span, StreamedSpan):
        previous_span = scope.span
        scope.span = span
        try:
            yield span
        finally:
            scope.span = previous_span
        return

    previous_streamed_span = scope.streamed_span
    scope.streamed_span = span
    try:
        yield span
    finally:
        scope.streamed_span = previous_streamed_span


def _patch_botocore_client() -> None:
    from sentry_sdk.integrations.boto3 import Boto3Integration

    orig_init = BaseClient.__init__
    orig_make_api_call = BaseClient._make_api_call  # type: ignore

    def sentry_patched_init(self: "BaseClient", *args: "Any", **kwargs: "Any") -> None:
        orig_init(self, *args, **kwargs)
        meta = self.meta
        meta.events.register("request-created", _sentry_request_created)
        # run after other `before-sign` handlers so existing baggage is preserved.
        meta.events.register_last("before-sign", _sentry_before_sign)

    def sentry_patched_make_api_call(
        self: "BaseClient", operation_name: str, api_params: "Any"
    ) -> "Any":
        """
        Track a single API call, including retries, serialization, and endpoint
        resolution. For streaming responses, keep the span open until the
        response body is consumed or closed.
        https://github.com/boto/botocore/blob/develop/botocore/client.py
        https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        """
        client = sentry_sdk.get_client()
        if client.get_integration(Boto3Integration) is None:
            return orig_make_api_call(self, operation_name, api_params)

        ctx = AwsCallContext(operation_name, api_params)

        # add optional metadata to context.
        with capture_internal_exceptions():
            ctx.add_metadata(self)

        span: "Optional[Union[Span, StreamedSpan]]" = None
        with capture_internal_exceptions():
            span = _start_client_span(ctx)

        if span is None:
            return orig_make_api_call(self, operation_name, api_params)

        # activate without finishing; a streaming response may outlive the call.
        span_ctx = _activate_client_span(span)

        try:
            with span_ctx:
                parsed = orig_make_api_call(self, operation_name, api_params)
        except BaseException as error:
            _finish_span(span, error)
            raise

        streaming_body_instrumented = False
        with capture_internal_exceptions():
            streaming_body_instrumented = _instrument_streaming_body(span, parsed)
        streaming_body_instrumented = False
        with capture_internal_exceptions():
            streaming_body_instrumented = _instrument_streaming_body(span, parsed)

        # `StreamingBody`s finish their span when consumed or closed.
        if not streaming_body_instrumented:
            _finish_span(span)
        return parsed

    BaseClient.__init__ = sentry_patched_init  # type: ignore
    BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore

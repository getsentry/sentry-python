from contextlib import contextmanager
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
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
from sentry_sdk.integrations.boto3._services.registry import (
    _resolve_service,
)
from sentry_sdk.integrations.boto3.consts import IDENTIFIER, ORIGIN
from sentry_sdk.traces import NoOpSpan, Span, SpanStatus
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Iterator, Optional, Tuple

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

try:
    from botocore.client import BaseClient
    from botocore.exceptions import ClientError
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


def _register_request_hooks(client: "BaseClient") -> None:
    # aiobotocore clients also run botocore's `__init__`; `unique_id` registers each hook once.
    client.meta.events.register(
        "request-created", _sentry_request_created, unique_id="sentry-request-created"
    )
    # run after other `before-sign` handlers so existing baggage is preserved.
    client.meta.events.register_last(
        "before-sign", _sentry_before_sign, unique_id="sentry-before-sign"
    )


def _start_api_call_span(
    client: "BaseClient",
    operation_name: str,
    api_params: "Dict[str, Any]",
    origin: str,
) -> "Optional[Tuple[Span, AwsCallContext, Optional[_ServiceExtension]]]":
    ctx = AwsCallContext(operation_name, api_params)
    with capture_internal_exceptions():
        # add optional metadata to the context, e.g. service-name, region-name, etc.
        ctx.add_metadata(client)
    service_ext = _resolve_service(ctx.service_name)
    span = _start_client_span(ctx, service_ext, origin)
    if span is None:
        return None
    return span, ctx, service_ext


def _set_error_attributes(
    span: "Span",
    ctx: "AwsCallContext",
    service_ext: "Optional[_ServiceExtension]",
    error: "BaseException",
) -> None:
    attributes: "Attributes" = {}
    if service_ext is not None and isinstance(error, ClientError):
        with capture_internal_exceptions():
            attributes.update(service_ext.get_response_attributes(ctx, error.response))
    with capture_internal_exceptions():
        attributes.update(_get_error_attributes(error))
    with capture_internal_exceptions():
        span.set_attributes(attributes)


def _set_response_attributes(
    span: "Span",
    ctx: "AwsCallContext",
    service_ext: "Optional[_ServiceExtension]",
    parsed: "Dict[str, Any]",
) -> None:
    attributes: "Attributes" = {}
    if service_ext is not None:
        with capture_internal_exceptions():
            attributes.update(service_ext.get_response_attributes(ctx, parsed))
    with capture_internal_exceptions():
        attributes.update(_get_response_attributes(parsed))
    with capture_internal_exceptions():
        span.set_attributes(attributes)
        # a service extension reports a failure that didn't raise, i.e. one
        # in a successful response, with `error.type`.
        if SPANDATA.ERROR_TYPE in attributes:
            span.status = SpanStatus.ERROR


def _patch_botocore_client() -> None:
    orig_init = BaseClient.__init__
    orig_make_api_call = BaseClient._make_api_call  # type: ignore

    def sentry_patched_init(self: "BaseClient", *args: "Any", **kwargs: "Any") -> None:
        orig_init(self, *args, **kwargs)
        with capture_internal_exceptions():
            _register_request_hooks(self)

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
        client = sentry_sdk.get_client()
        if client.get_integration(IDENTIFIER) is None:
            return orig_make_api_call(self, operation_name, api_params)

        api_call_span = None
        with capture_internal_exceptions():
            api_call_span = _start_api_call_span(
                self, operation_name, api_params, ORIGIN
            )

        if api_call_span is None:
            return orig_make_api_call(self, operation_name, api_params)

        span, ctx, service_ext = api_call_span
        # activate without finishing; a streaming response may outlive the call.
        try:
            with _activate_client_span(span):
                try:
                    parsed = orig_make_api_call(self, operation_name, api_params)
                except BaseException as error:
                    _set_error_attributes(span, ctx, service_ext, error)
                    raise
                _set_response_attributes(span, ctx, service_ext, parsed)
        except BaseException as error:
            _finish_span(span, error)
            raise

        streaming_body_instrumented = False
        with capture_internal_exceptions():
            streaming_body_instrumented = _instrument_streaming_body(span, parsed)

        # `StreamingBody`s finish their span when consumed or closed.
        if not streaming_body_instrumented:
            _finish_span(span)
        return parsed

    BaseClient.__init__ = sentry_patched_init  # type: ignore
    BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore

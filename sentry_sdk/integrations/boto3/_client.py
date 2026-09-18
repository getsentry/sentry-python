from contextlib import contextmanager
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3._context import AwsCallContext
from sentry_sdk.integrations.boto3._instrumentation import (
    _finish_span,
    _get_error_attributes,
    _get_response_attributes,
    _instrument_streaming_body,
    _sentry_before_sign,
    _sentry_request_created,
    _set_span_attributes,
    _start_client_span,
)
from sentry_sdk.integrations.boto3._services.registry import (
    _resolve_service,
)
from sentry_sdk.traces import NoOpStreamedSpan, StreamedSpan
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Iterator, Optional, Union

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
    from sentry_sdk.tracing import Span

try:
    from botocore.client import BaseClient
    from botocore.exceptions import ClientError
except ImportError:
    raise DidNotEnable("botocore not installed")


@contextmanager
def _activate_client_span(span: "StreamedSpan") -> "Iterator[StreamedSpan]":
    """Temporarily activate an inactive boto span without ending it."""
    if isinstance(span, NoOpStreamedSpan):
        yield span
        return

    scope = sentry_sdk.get_current_scope()
    previous_span = scope.streamed_span
    scope.streamed_span = span
    try:
        yield span
    finally:
        scope.streamed_span = previous_span


def _patch_botocore_client() -> None:
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

        service_ext: "Optional[_ServiceExtension]" = None
        with capture_internal_exceptions():
            # resolve service extension for service-specific enrichment.
            service_ext = _resolve_service(ctx.service_name)

        span: "Optional[Union[Span, StreamedSpan]]" = None
        with capture_internal_exceptions():
            span = _start_client_span(ctx, service_ext)

        if span is None:
            return orig_make_api_call(self, operation_name, api_params)

        # activate without finishing; a streaming response may outlive the call.
        span_ctx = (
            _activate_client_span(span) if isinstance(span, StreamedSpan) else span
        )

        attributes: "Attributes" = {}
        try:
            with span_ctx:
                try:
                    parsed = orig_make_api_call(self, operation_name, api_params)
                except BaseException as error:
                    if service_ext is not None and isinstance(error, ClientError):
                        with capture_internal_exceptions():
                            attributes.update(
                                service_ext.get_response_attributes(ctx, error.response)
                            )
                    # generic attributes outweigh service-specific attributes.
                    with capture_internal_exceptions():
                        attributes.update(_get_error_attributes(error))
                    raise
                else:
                    if service_ext is not None:
                        with capture_internal_exceptions():
                            attributes.update(
                                service_ext.get_response_attributes(ctx, parsed)
                            )
                    with capture_internal_exceptions():
                        attributes.update(_get_response_attributes(parsed))
                finally:
                    # enrich before the static span's context manager finishes it.
                    with capture_internal_exceptions():
                        _set_span_attributes(span, attributes)
        except BaseException as error:
            # finish `StreamedSpan` explicitly; static spans are finished by
            # their context manager.
            if isinstance(span, StreamedSpan):
                _finish_span(span, error)
            raise

        streaming_body_instrumented = False
        with capture_internal_exceptions():
            streaming_body_instrumented = _instrument_streaming_body(span, parsed)

        # `StreamingBody`s finish their span when consumed or closed.
        if isinstance(span, StreamedSpan) and not streaming_body_instrumented:
            _finish_span(span)
        return parsed

    BaseClient.__init__ = sentry_patched_init  # type: ignore
    BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore

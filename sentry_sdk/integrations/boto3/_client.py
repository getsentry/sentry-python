from typing import TYPE_CHECKING

from botocore.client import BaseClient

import sentry_sdk
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3._context import AwsCallContext
from sentry_sdk.integrations.boto3._instrumentation import (
    _finish_client_span,
    _finish_client_span_with_error,
    _sentry_before_sign,
    _sentry_request_created,
    _start_client_span,
)
from sentry_sdk.integrations.boto3._services.registry import (
    _resolve_service_extension,
)
from sentry_sdk.traces import NoOpStreamedSpan
from sentry_sdk.tracing import NoOpSpan
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
    from sentry_sdk.traces import StreamedSpan
    from sentry_sdk.tracing import Span


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
        Own the span lifecycle for one `_make_api_call()` invocation, including
        all retries performed by botocore.

        Botocore's ``after-call-error`` event only surrounds ``_make_request``.
        Wrapping ``_make_api_call`` also closes the span when parameter building,
        serialization, or endpoint resolution fails before the request starts:
        https://github.com/boto/botocore/blob/develop/botocore/client.py

        https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        """
        client = sentry_sdk.get_client()
        if client.get_integration(Boto3Integration) is None:
            return orig_make_api_call(self, operation_name, api_params)

        ctx: "Optional[AwsCallContext]" = None
        service_extension: "Optional[_ServiceExtension]" = None
        span: "Optional[Union[Span, StreamedSpan]]" = None

        with capture_internal_exceptions():
            ctx = AwsCallContext(self, operation_name, api_params)

        if ctx is not None:
            # The resolver contains its own fail-open import boundary.
            service_extension = _resolve_service_extension(ctx.service_name)

            with capture_internal_exceptions():
                span = _start_client_span(ctx, service_extension)
                if span is not None:
                    span.__enter__()

        instrumented_api_params = api_params
        if (
            ctx is not None
            and service_extension is not None
            and span is not None
            and not isinstance(span, (NoOpSpan, NoOpStreamedSpan))
            and client.options.get("propagate_traces")
            and isinstance(api_params, dict)
        ):
            with capture_internal_exceptions():
                # propagation must use current scope
                headers = dict(
                    sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                        span=span
                    )
                )
                propagated_params = service_extension.inject_trace_context(
                    ctx,
                    headers,
                )
                # Only pass a service extension's replacement when it is a mapping.
                # Otherwise preserve the caller's parameters and Botocore behavior.
                if isinstance(propagated_params, dict):
                    instrumented_api_params = propagated_params

        try:
            parsed = orig_make_api_call(self, operation_name, instrumented_api_params)
        except BaseException as exc:
            if span is not None:
                with capture_internal_exceptions():
                    _finish_client_span_with_error(
                        span,
                        exc,
                        ctx,
                        service_extension,
                    )
            raise

        if span is not None:
            with capture_internal_exceptions():
                _finish_client_span(
                    span,
                    parsed,
                    ctx,
                    service_extension,
                )
        return parsed

    BaseClient.__init__ = sentry_patched_init  # type: ignore
    BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore

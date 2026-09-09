from typing import TYPE_CHECKING

from botocore.client import BaseClient

import sentry_sdk
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3._instrumentation import (
    _finish_client_span,
    _finish_client_span_with_error,
    _sentry_before_sign,
    _sentry_request_created,
    _start_client_span,
)
from sentry_sdk.integrations.boto3._services import (
    _resolve_service_extension,
)
from sentry_sdk.traces import NoOpStreamedSpan
from sentry_sdk.tracing import NoOpSpan
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Union

    from botocore.model import ServiceId

    from sentry_sdk.integrations.boto3._services import _ServiceExtension
    from sentry_sdk.traces import StreamedSpan
    from sentry_sdk.tracing import Span


class _ClientCallContext:
    """Inputs and client metadata for a single botocore client call."""

    __slots__ = (
        "client",
        "service_name",
        "service_id",
        "operation_name",
        "region_name",
        "endpoint_url",
        "api_version",
        "api_params",
    )

    def __init__(
        self,
        client: "BaseClient",
        operation_name: str,
        api_params: "Any",
    ) -> None:
        client_meta = client.meta
        service_model = client_meta.service_model

        self.client: "BaseClient" = client
        # botocore's internal identifier, e.g. `apigateway`.
        self.service_name: str = service_model.service_name
        # modeled AWS service identity used in span names, e.g. `API Gateway`.
        self.service_id: "ServiceId" = service_model.service_id
        self.operation_name: str = operation_name
        self.region_name: "Optional[str]" = getattr(client_meta, "region_name", None)
        self.endpoint_url: "Optional[str]" = getattr(client_meta, "endpoint_url", None)
        self.api_version: str = service_model.api_version
        # params may contain streams or arbitrary SDK objects; make a shallow copy
        # so nested values preserve original identity.
        self.api_params: "Dict[str, Any]" = (
            dict(api_params) if isinstance(api_params, dict) else {}
        )


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
        https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        """
        client = sentry_sdk.get_client()
        if client.get_integration(Boto3Integration) is None:
            return orig_make_api_call(self, operation_name, api_params)

        call_context: "Optional[_ClientCallContext]" = None
        service_extension: "Optional[_ServiceExtension]" = None
        span: "Optional[Union[Span, StreamedSpan]]" = None

        with capture_internal_exceptions():
            call_context = _ClientCallContext(self, operation_name, api_params)

        if call_context is not None:
            with capture_internal_exceptions():
                service_extension = _resolve_service_extension(
                    call_context.service_name
                )

            with capture_internal_exceptions():
                span = _start_client_span(call_context, service_extension)
                if span is not None:
                    span.__enter__()

        instrumented_api_params = api_params
        if (
            call_context is not None
            and service_extension is not None
            and span is not None
            and not isinstance(span, (NoOpSpan, NoOpStreamedSpan))
            and client.options.get("propagate_traces")
        ):
            with capture_internal_exceptions():
                # propagation must use current scope
                headers = dict(
                    sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                        span=span
                    )
                )
                propagated_params = service_extension.inject_trace_context(
                    call_context,
                    api_params,
                    headers,
                )
                # botocore does not validate the parameters, so we must do it here.
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
                        call_context,
                        service_extension,
                    )
            raise

        if span is not None:
            with capture_internal_exceptions():
                _finish_client_span(
                    span,
                    parsed,
                    call_context,
                    service_extension,
                )
        return parsed

    BaseClient.__init__ = sentry_patched_init  # type: ignore
    BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore

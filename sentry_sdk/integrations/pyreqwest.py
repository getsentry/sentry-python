from contextlib import contextmanager
from typing import Any, Generator

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import (
    add_http_request_source,
    propagate_trace_headers,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    parse_url,
)

try:
    from pyreqwest.client import (  # type: ignore[import-not-found]
        ClientBuilder,
        SyncClientBuilder,
    )
    from pyreqwest.middleware import Next, SyncNext  # type: ignore[import-not-found]
    from pyreqwest.request import (  # type: ignore[import-not-found]
        OneOffRequestBuilder,
        Request,
        SyncOneOffRequestBuilder,
    )
    from pyreqwest.response import (  # type: ignore[import-not-found]
        Response,
        SyncResponse,
    )
except ImportError:
    raise DidNotEnable("pyreqwest not installed or incompatible version installed")


class PyreqwestIntegration(Integration):
    identifier = "pyreqwest"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        _patch_pyreqwest()


def _patch_pyreqwest() -> None:
    # Patch Client Builders
    _patch_builder_method(ClientBuilder, "build", sentry_async_middleware)
    _patch_builder_method(SyncClientBuilder, "build", sentry_sync_middleware)

    # Patch Request Builders
    _patch_builder_method(OneOffRequestBuilder, "send", sentry_async_middleware)
    _patch_builder_method(SyncOneOffRequestBuilder, "send", sentry_sync_middleware)


def _patch_builder_method(cls: type, method_name: str, middleware: "Any") -> None:
    if not hasattr(cls, method_name):
        return

    original_method = getattr(cls, method_name)

    def sentry_patched_method(self: "Any", *args: "Any", **kwargs: "Any") -> "Any":
        if not getattr(self, "_sentry_instrumented", False):
            integration = sentry_sdk.get_client().get_integration(PyreqwestIntegration)
            if integration is not None:
                self.with_middleware(middleware)
                try:
                    self._sentry_instrumented = True
                except (TypeError, AttributeError):
                    # In case the instance itself is immutable or doesn't allow extra attributes
                    pass
        return original_method(self, *args, **kwargs)

    setattr(cls, method_name, sentry_patched_method)


@contextmanager
def _sentry_pyreqwest_span(request: "Request") -> "Generator[Any, None, None]":
    parsed_url = None
    with capture_internal_exceptions():
        parsed_url = parse_url(str(request.url), sanitize=False)

    if sentry_sdk.traces.get_current_span() is None:
        propagate_trace_headers(client=sentry_sdk.get_client(), request=request)
        yield None
        return

    with sentry_sdk.traces.start_span(
        name=f"{request.method} {parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE}",
        attributes={
            "sentry.op": OP.HTTP_CLIENT,
            "sentry.origin": PyreqwestIntegration.origin,
            SPANDATA.HTTP_REQUEST_METHOD: request.method,
        },
    ) as span:
        if parsed_url is not None and should_send_default_pii():
            span.set_attribute(SPANDATA.URL_FULL, parsed_url.url)
            span.set_attribute(SPANDATA.URL_QUERY, parsed_url.query)
            span.set_attribute(SPANDATA.URL_FRAGMENT, parsed_url.fragment)

        propagate_trace_headers(client=sentry_sdk.get_client(), request=request)

        yield span

        if span is not None:
            with capture_internal_exceptions():
                add_http_request_source(span)


async def sentry_async_middleware(
    request: "Request", next_handler: "Next"
) -> "Response":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return await next_handler.run(request)

    with _sentry_pyreqwest_span(request) as span:
        response = await next_handler.run(request)
        if span is not None:
            span.status = "error" if response.status >= 400 else "ok"
            span.set_attribute(
                SPANDATA.HTTP_STATUS_CODE,
                response.status,
            )

    return response


def sentry_sync_middleware(
    request: "Request", next_handler: "SyncNext"
) -> "SyncResponse":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return next_handler.run(request)

    with _sentry_pyreqwest_span(request) as span:
        response = next_handler.run(request)
        if span is not None:
            span.status = "error" if response.status >= 400 else "ok"
            span.set_attribute(
                SPANDATA.HTTP_STATUS_CODE,
                response.status,
            )

    return response

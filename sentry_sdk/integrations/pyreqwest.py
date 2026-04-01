import sentry_sdk
from sentry_sdk import start_span
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME
from sentry_sdk.tracing_utils import (
    should_propagate_trace,
    add_http_request_source,
    add_sentry_baggage_to_headers,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    logger,
    parse_url,
)

from contextlib import contextmanager
from typing import Any, Generator

try:
    from pyreqwest.client import ClientBuilder, SyncClientBuilder  # type: ignore[import-not-found]
    from pyreqwest.request import (  # type: ignore[import-not-found]
        Request,
        OneOffRequestBuilder,
        SyncOneOffRequestBuilder,
    )
    from pyreqwest.middleware import Next, SyncNext  # type: ignore[import-not-found]
    from pyreqwest.response import Response, SyncResponse  # type: ignore[import-not-found]
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

    with start_span(
        op=OP.HTTP_CLIENT,
        name=f"{request.method} {parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE}",
        origin=PyreqwestIntegration.origin,
    ) as span:
        span.set_data(SPANDATA.HTTP_METHOD, request.method)
        if parsed_url is not None:
            span.set_data("url", parsed_url.url)
            span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
            span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

        if should_propagate_trace(sentry_sdk.get_client(), str(request.url)):
            for (
                key,
                value,
            ) in sentry_sdk.get_current_scope().iter_trace_propagation_headers():
                logger.debug(
                    "[Tracing] Adding `{key}` header {value} to outgoing request to {url}.".format(
                        key=key, value=value, url=request.url
                    )
                )

                if key == BAGGAGE_HEADER_NAME:
                    add_sentry_baggage_to_headers(request.headers, value)
                else:
                    request.headers[key] = value

        yield span

    with capture_internal_exceptions():
        add_http_request_source(span)


async def sentry_async_middleware(
    request: "Request", next_handler: "Next"
) -> "Response":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return await next_handler.run(request)

    with _sentry_pyreqwest_span(request) as span:
        response = await next_handler.run(request)
        span.set_http_status(response.status)

    return response


def sentry_sync_middleware(
    request: "Request", next_handler: "SyncNext"
) -> "SyncResponse":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return next_handler.run(request)

    with _sentry_pyreqwest_span(request) as span:
        response = next_handler.run(request)
        span.set_http_status(response.status)

    return response

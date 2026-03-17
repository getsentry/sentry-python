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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


import importlib.util

if importlib.util.find_spec("pyreqwest") is None:
    raise DidNotEnable("pyreqwest is not installed")


class PyreqwestIntegration(Integration):
    identifier = "pyreqwest"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        _patch_pyreqwest()


def _patch_pyreqwest() -> None:
    # Patch Client Builders
    try:
        from pyreqwest.client import ClientBuilder, SyncClientBuilder  # type: ignore[import-not-found]

        _patch_builder_method(ClientBuilder, "build", sentry_async_middleware)
        _patch_builder_method(SyncClientBuilder, "build", sentry_sync_middleware)
    except ImportError:
        pass

    # Patch Request Builders (for simple requests and manual request building)
    try:
        from pyreqwest.request import (  # type: ignore[import-not-found]
            RequestBuilder,
            SyncRequestBuilder,
            OneOffRequestBuilder,
            SyncOneOffRequestBuilder,
        )

        _patch_builder_method(RequestBuilder, "build", sentry_async_middleware)
        _patch_builder_method(RequestBuilder, "build_streamed", sentry_async_middleware)
        _patch_builder_method(SyncRequestBuilder, "build", sentry_sync_middleware)
        _patch_builder_method(
            SyncRequestBuilder, "build_streamed", sentry_sync_middleware
        )
        _patch_builder_method(OneOffRequestBuilder, "send", sentry_async_middleware)
        _patch_builder_method(SyncOneOffRequestBuilder, "send", sentry_sync_middleware)
    except ImportError:
        pass


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


async def sentry_async_middleware(request: "Any", next_handler: "Any") -> "Any":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return await next_handler.run(request)

    parsed_url = None
    with capture_internal_exceptions():
        parsed_url = parse_url(str(request.url), sanitize=False)

    with start_span(
        op=OP.HTTP_CLIENT,
        name="%s %s"
        % (
            request.method,
            parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
        ),
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

        response = await next_handler.run(request)

        span.set_http_status(response.status)

    with capture_internal_exceptions():
        add_http_request_source(span)

    return response


def sentry_sync_middleware(request: "Any", next_handler: "Any") -> "Any":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return next_handler.run(request)

    parsed_url = None
    with capture_internal_exceptions():
        parsed_url = parse_url(str(request.url), sanitize=False)

    with start_span(
        op=OP.HTTP_CLIENT,
        name="%s %s"
        % (
            request.method,
            parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
        ),
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

        response = next_handler.run(request)

        span.set_http_status(response.status)

    with capture_internal_exceptions():
        add_http_request_source(span)

    return response

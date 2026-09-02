from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Generator

import sentry_sdk
from sentry_sdk import start_span
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME
from sentry_sdk.tracing_utils import (
    add_http_breadcrumb,
    add_http_request_source,
    add_sentry_baggage_to_headers,
    get_url_attributes,
    has_span_streaming_enabled,
    propagate_trace_headers,
    should_propagate_trace,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    logger,
    parse_url,
)

if TYPE_CHECKING:
    from typing import Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.utils import ParsedUrl

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
        integration = sentry_sdk.get_client().get_integration(PyreqwestIntegration)

        if getattr(self, "_sentry_instrumented", False) or integration is None:
            return original_method(self, *args, **kwargs)

        self.with_middleware(middleware)

        try:
            self._sentry_instrumented = True
        except (TypeError, AttributeError):
            # In case the instance itself is immutable or doesn't allow extra attributes
            pass

        return original_method(self, *args, **kwargs)

    setattr(cls, method_name, sentry_patched_method)


def _get_breadcrumb_url_data(
    parsed_url: "Optional[ParsedUrl]", url_attributes: "Attributes"
) -> "dict[str, Any]":
    if parsed_url is None or not url_attributes:
        return {}

    # Legacy spans keep the bare URL in breadcrumbs; only span streaming
    # reports the full URL
    url = parsed_url.url
    if has_span_streaming_enabled(sentry_sdk.get_client().options):
        url = url_attributes.get(SPANDATA.URL_FULL, url)

    return {
        "url": url,
        SPANDATA.HTTP_QUERY: url_attributes.get(SPANDATA.URL_QUERY, ""),
        SPANDATA.HTTP_FRAGMENT: url_attributes.get(SPANDATA.URL_FRAGMENT, ""),
    }


@contextmanager
def _sentry_pyreqwest_span(
    request: "Request", url_attributes: "Attributes"
) -> "Generator[Any, None, None]":
    parsed_url = None
    with capture_internal_exceptions():
        parsed_url = parse_url(str(request.url), sanitize=False)

    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
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
            for key, value in url_attributes.items():
                span.set_attribute(key, value)

            propagate_trace_headers(client=sentry_sdk.get_client(), request=request)

            yield span

            if span is not None:
                with capture_internal_exceptions():
                    add_http_request_source(span)

            return

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
    request: "Request",
    next_handler: "Next",
) -> "Response":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return await next_handler.run(request)

    method = request.method
    parsed_url = None
    with capture_internal_exceptions():
        # This needs to be done early because the URL is no longer accessible
        # after the request has been sent
        parsed_url = parse_url(str(request.url), sanitize=False)

    url_attributes = get_url_attributes(sentry_sdk.get_client(), parsed_url)

    response = None
    with _sentry_pyreqwest_span(request, url_attributes) as span:
        response = await next_handler.run(request)
        if isinstance(span, StreamedSpan):
            span.status = "error" if response.status >= 400 else "ok"
            span.set_attribute(
                SPANDATA.HTTP_STATUS_CODE,
                response.status,
            )
        elif span is not None:
            span.set_http_status(response.status)

    if response is not None:
        breadcrumb_data = {
            SPANDATA.HTTP_METHOD: method,
            SPANDATA.HTTP_STATUS_CODE: response.status,
        }

        breadcrumb_data.update(_get_breadcrumb_url_data(parsed_url, url_attributes))

        add_http_breadcrumb(response.status, breadcrumb_data)

    return response


def sentry_sync_middleware(
    request: "Request", next_handler: "SyncNext"
) -> "SyncResponse":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return next_handler.run(request)

    method = request.method
    parsed_url = None
    with capture_internal_exceptions():
        # This needs to be done early because the URL is no longer accessible
        # after the request has been sent
        parsed_url = parse_url(str(request.url), sanitize=False)

    url_attributes = get_url_attributes(sentry_sdk.get_client(), parsed_url)

    response = None
    with _sentry_pyreqwest_span(request, url_attributes) as span:
        response = next_handler.run(request)
        if isinstance(span, StreamedSpan):
            span.status = "error" if response.status >= 400 else "ok"
            span.set_attribute(
                SPANDATA.HTTP_STATUS_CODE,
                response.status,
            )
        elif span is not None:
            span.set_http_status(response.status)

    if response is not None:
        breadcrumb_data = {
            SPANDATA.HTTP_METHOD: method,
            SPANDATA.HTTP_STATUS_CODE: response.status,
        }

        breadcrumb_data.update(_get_breadcrumb_url_data(parsed_url, url_attributes))

        add_http_breadcrumb(response.status, breadcrumb_data)

    return response

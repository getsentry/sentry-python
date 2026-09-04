from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Generator

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.tracing_utils import (
    add_http_breadcrumb,
    add_http_request_source,
    get_url_attributes,
    has_span_streaming_enabled,
    propagate_trace_headers,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    parse_url,
    parse_version,
)

if TYPE_CHECKING:
    from typing import Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.utils import ParsedUrl

try:
    from pyreqwest import (  # type: ignore[import-not-found]
        __version__ as PYREQWEST_VERSION,
    )
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
    raise DidNotEnable("pyreqwest not installed or incompatible")


class PyreqwestIntegration(Integration):
    identifier = "pyreqwest"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        version = parse_version(PYREQWEST_VERSION)
        _check_minimum_version(PyreqwestIntegration, version)

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
        if span is not None:
            span.status = "error" if response.status >= 400 else "ok"
            span.set_attribute(
                SPANDATA.HTTP_STATUS_CODE,
                response.status,
            )

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
        if span is not None:
            span.status = "error" if response.status >= 400 else "ok"
            span.set_attribute(
                SPANDATA.HTTP_STATUS_CODE,
                response.status,
            )

    if response is not None:
        breadcrumb_data = {
            SPANDATA.HTTP_METHOD: method,
            SPANDATA.HTTP_STATUS_CODE: response.status,
        }

        breadcrumb_data.update(_get_breadcrumb_url_data(parsed_url, url_attributes))

        add_http_breadcrumb(response.status, breadcrumb_data)

    return response

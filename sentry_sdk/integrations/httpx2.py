from contextlib import nullcontext
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.tracing_utils import (
    add_http_breadcrumb,
    add_http_request_source,
    get_url_attributes,
    propagate_trace_headers,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    ensure_integration_enabled,
    parse_url,
    parse_version,
)

if TYPE_CHECKING:
    from typing import Any


try:
    from httpx2 import AsyncClient, Client, Request, Response
    from httpx2 import __version__ as HTTPX2_VERSION
except ImportError:
    raise DidNotEnable("httpx2 is not installed or incompatible")

__all__ = ["Httpx2Integration"]


class Httpx2Integration(Integration):
    identifier = "httpx2"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        """
        httpx2 has its own transport layer and can be customized when needed,
        so patch Client.send and AsyncClient.send to support both synchronous and async interfaces.
        """
        version = parse_version(HTTPX2_VERSION)
        _check_minimum_version(Httpx2Integration, version)

        _install_httpx2_client()
        _install_httpx2_async_client()


def _install_httpx2_client() -> None:
    real_send = Client.send

    @ensure_integration_enabled(Httpx2Integration, real_send)
    def send(self: "Client", request: "Request", **kwargs: "Any") -> "Response":
        client = sentry_sdk.get_client()

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        url_attributes = get_url_attributes(client, parsed_url)

        if sentry_sdk.traces.get_current_span() is None:
            span_ctx = nullcontext()
        else:
            span_ctx = sentry_sdk.traces.start_span(
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                attributes={
                    "sentry.op": OP.HTTP_CLIENT,
                    "sentry.origin": Httpx2Integration.origin,
                    "http.request.method": request.method,
                },
            )

        with span_ctx as span:
            propagate_trace_headers(client, request)

            try:
                rv = real_send(self, request, **kwargs)

                if span is not None:
                    span.status = "error" if rv.status_code >= 400 else "ok"
                    span.set_attribute("http.response.status_code", rv.status_code)
            finally:
                if span is not None:
                    span.set_attributes(url_attributes)

            if span is not None:
                # Needs to happen within the context manager as we want to attach the
                # final data before the span finishes and is sent for ingesting.
                with capture_internal_exceptions():
                    add_http_request_source(span)

        breadcrumb_data = {
            SPANDATA.HTTP_METHOD: request.method,
            SPANDATA.HTTP_STATUS_CODE: rv.status_code,
            "reason": rv.reason_phrase,
        }

        if parsed_url and url_attributes:
            breadcrumb_data.update(
                {
                    "url": url_attributes.get("url.full", ""),
                    SPANDATA.HTTP_QUERY: url_attributes.get("url.query", ""),
                    SPANDATA.HTTP_FRAGMENT: url_attributes.get("url.fragment", ""),
                }
            )

        add_http_breadcrumb(rv.status_code, breadcrumb_data)

        return rv

    Client.send = send  # type: ignore


def _install_httpx2_async_client() -> None:
    real_send = AsyncClient.send

    async def send(
        self: "AsyncClient", request: "Request", **kwargs: "Any"
    ) -> "Response":
        client = sentry_sdk.get_client()
        if client.get_integration(Httpx2Integration) is None:
            return await real_send(self, request, **kwargs)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        if sentry_sdk.traces.get_current_span() is None:
            span_ctx = nullcontext()
        else:
            span_ctx = sentry_sdk.traces.start_span(
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                attributes={
                    "sentry.op": OP.HTTP_CLIENT,
                    "sentry.origin": Httpx2Integration.origin,
                    "http.request.method": request.method,
                },
            )

        url_attributes = get_url_attributes(client, parsed_url)

        with span_ctx as span:
            propagate_trace_headers(client, request)

            try:
                rv = await real_send(self, request, **kwargs)

                if span is not None:
                    span.status = "error" if rv.status_code >= 400 else "ok"
                    span.set_attribute("http.response.status_code", rv.status_code)
            finally:
                if span is not None:
                    span.set_attributes(url_attributes)

            if span is not None:
                # Needs to happen within the context manager as we want to attach the
                # final data before the span finishes and is sent for ingesting.
                with capture_internal_exceptions():
                    add_http_request_source(span)

        breadcrumb_data = {
            SPANDATA.HTTP_METHOD: request.method,
            SPANDATA.HTTP_STATUS_CODE: rv.status_code,
            "reason": rv.reason_phrase,
        }
        if parsed_url and url_attributes:
            breadcrumb_data.update(
                {
                    "url": url_attributes.get("url.full", ""),
                    SPANDATA.HTTP_QUERY: url_attributes.get("url.query", ""),
                    SPANDATA.HTTP_FRAGMENT: url_attributes.get("url.fragment", ""),
                }
            )

        add_http_breadcrumb(rv.status_code, breadcrumb_data)

        return rv

    AsyncClient.send = send  # type: ignore

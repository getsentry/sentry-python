import sentry_sdk
from sentry_sdk import start_span
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, Span
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import (
    should_propagate_trace,
    add_http_request_source,
    add_sentry_baggage_to_headers,
    has_span_streaming_enabled,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    ensure_integration_enabled,
    logger,
    parse_url,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Union


try:
    from httpx import AsyncClient, Client, Request, Response  # type: ignore
except ImportError:
    raise DidNotEnable("httpx is not installed")

__all__ = ["HttpxIntegration"]


class HttpxIntegration(Integration):
    identifier = "httpx"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        """
        httpx has its own transport layer and can be customized when needed,
        so patch Client.send and AsyncClient.send to support both synchronous and async interfaces.
        """
        _install_httpx_client()
        _install_httpx_async_client()


def _install_httpx_client() -> None:
    real_send = Client.send

    def send(self: "Client", request: "Request", **kwargs: "Any") -> "Response":
        client = sentry_sdk.get_client()
        if client.get_integration(HttpxIntegration) is None:
            return real_send(self, request, **kwargs)

        span_streaming = has_span_streaming_enabled(client.options)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        span_ctx: "Optional[Union[Span, StreamedSpan]]" = None
        if span_streaming:
            span_ctx = sentry_sdk.traces.start_span(
                name=f"{request.method} {parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE}"
            )
        else:
            span_ctx = start_span(
                op=OP.HTTP_CLIENT,
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                origin=HttpxIntegration.origin,
            )

        with span_ctx as span:
            if isinstance(span, StreamedSpan):
                span.set_attribute("sentry.op", OP.HTTP_CLIENT)
                span.set_attribute("sentry.origin", HttpxIntegration.origin)

                span.set_attribute(SPANDATA.HTTP_METHOD, request.method)
                if parsed_url is not None:
                    span.set_attribute("url", parsed_url.url)
                    span.set_attribute(SPANDATA.HTTP_QUERY, parsed_url.query)
                    span.set_attribute(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)
            else:
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

            rv = real_send(self, request, **kwargs)

            span.set_http_status(rv.status_code)
            if isinstance(span, StreamedSpan):
                span.set_attribute("reason", rv.reason_phrase)
            else:
                span.set_data("reason", rv.reason_phrase)

        with capture_internal_exceptions():
            add_http_request_source(span)

        return rv

    Client.send = send


def _install_httpx_async_client() -> None:
    real_send = AsyncClient.send

    async def send(
        self: "AsyncClient", request: "Request", **kwargs: "Any"
    ) -> "Response":
        client = sentry_sdk.get_client()
        if client.get_integration(HttpxIntegration) is None:
            return await real_send(self, request, **kwargs)

        span_streaming = has_span_streaming_enabled(client.options)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        span_ctx: "Optional[Union[Span, StreamedSpan]]" = None
        if span_streaming:
            span_ctx = sentry_sdk.traces.start_span(
                name=f"{request.method} {parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE}"
            )
        else:
            span_ctx = start_span(
                op=OP.HTTP_CLIENT,
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                origin=HttpxIntegration.origin,
            )

        with span_ctx as span:
            if isinstance(span, StreamedSpan):
                span.set_attribute("sentry.op", OP.HTTP_CLIENT)
                span.set_attribute("sentry.origin", HttpxIntegration.origin)
                span.set_attribute(SPANDATA.HTTP_METHOD, request.method)
                if parsed_url is not None:
                    span.set_attribute("url", parsed_url.url)
                    span.set_attribute(SPANDATA.HTTP_QUERY, parsed_url.query)
                    span.set_attribute(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)
            else:
                span.set_data(SPANDATA.HTTP_METHOD, request.method)
                if parsed_url is not None:
                    span.set_data("url", parsed_url.url)
                    span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                    span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

            if should_propagate_trace(client, str(request.url)):
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

            rv = await real_send(self, request, **kwargs)

            span.set_http_status(rv.status_code)
            if isinstance(span, StreamedSpan):
                span.set_attribute("reason", rv.reason_phrase)
            else:
                span.set_data("reason", rv.reason_phrase)

        with capture_internal_exceptions():
            add_http_request_source(span)

        return rv

    AsyncClient.send = send

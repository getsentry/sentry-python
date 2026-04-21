import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME
from sentry_sdk.tracing_utils import (
    add_http_request_source,
    should_propagate_trace,
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
    from typing import Any
    from sentry_sdk._types import Attributes


try:
    from httpx import AsyncClient, Client, Request, Response
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

    @ensure_integration_enabled(HttpxIntegration, real_send)
    def send(self: "Client", request: "Request", **kwargs: "Any") -> "Response":
        client = sentry_sdk.get_client()
        is_span_streaming_enabled = has_span_streaming_enabled(client.options)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        if is_span_streaming_enabled:
            with sentry_sdk.traces.start_span(
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                attributes={
                    "sentry.op": OP.HTTP_CLIENT,
                    "sentry.origin": HttpxIntegration.origin,
                    "http.request.method": request.method,
                },
            ) as streamed_span:
                attributes: "Attributes" = {}

                if parsed_url is not None:
                    attributes["url.full"] = parsed_url.url
                    if parsed_url.query:
                        attributes["url.query"] = parsed_url.query
                    if parsed_url.fragment:
                        attributes["url.fragment"] = parsed_url.fragment

                if should_propagate_trace(client, str(request.url)):
                    for (
                        key,
                        value,
                    ) in (
                        sentry_sdk.get_current_scope().iter_trace_propagation_headers()
                    ):
                        logger.debug(
                            f"[Tracing] Adding `{key}` header {value} to outgoing request to {request.url}."
                        )

                        if key == BAGGAGE_HEADER_NAME:
                            add_sentry_baggage_to_headers(request.headers, value)
                        else:
                            request.headers[key] = value

                try:
                    rv = real_send(self, request, **kwargs)

                    streamed_span.status = "error" if rv.status_code >= 400 else "ok"
                    attributes["http.response.status_code"] = rv.status_code
                finally:
                    streamed_span.set_attributes(attributes)

                # Needs to happen within the context manager as we want to attach the
                # final data before the span finishes and is sent for ingesting.
                with capture_internal_exceptions():
                    add_http_request_source(streamed_span)
        else:
            with sentry_sdk.start_span(
                op=OP.HTTP_CLIENT,
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                origin=HttpxIntegration.origin,
            ) as span:
                span.set_data(SPANDATA.HTTP_METHOD, request.method)
                if parsed_url is not None:
                    span.set_data("url", parsed_url.url)
                    span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                    span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

                if should_propagate_trace(client, str(request.url)):
                    for (
                        key,
                        value,
                    ) in (
                        sentry_sdk.get_current_scope().iter_trace_propagation_headers()
                    ):
                        logger.debug(
                            f"[Tracing] Adding `{key}` header {value} to outgoing request to {request.url}."
                        )

                        if key == BAGGAGE_HEADER_NAME:
                            add_sentry_baggage_to_headers(request.headers, value)
                        else:
                            request.headers[key] = value

                rv = real_send(self, request, **kwargs)

                span.set_http_status(rv.status_code)
                span.set_data("reason", rv.reason_phrase)

            with capture_internal_exceptions():
                add_http_request_source(span)

        return rv

    Client.send = send  # type: ignore


def _install_httpx_async_client() -> None:
    real_send = AsyncClient.send

    async def send(
        self: "AsyncClient", request: "Request", **kwargs: "Any"
    ) -> "Response":
        client = sentry_sdk.get_client()
        if client.get_integration(HttpxIntegration) is None:
            return await real_send(self, request, **kwargs)

        is_span_streaming_enabled = has_span_streaming_enabled(client.options)
        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        if is_span_streaming_enabled:
            with sentry_sdk.traces.start_span(
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                attributes={
                    "sentry.op": OP.HTTP_CLIENT,
                    "sentry.origin": HttpxIntegration.origin,
                    "http.request.method": request.method,
                },
            ) as streamed_span:
                attributes: "Attributes" = {}

                if parsed_url is not None:
                    attributes["url.full"] = parsed_url.url
                    if parsed_url.query:
                        attributes["url.query"] = parsed_url.query
                    if parsed_url.fragment:
                        attributes["url.fragment"] = parsed_url.fragment

                if should_propagate_trace(client, str(request.url)):
                    for (
                        key,
                        value,
                    ) in (
                        sentry_sdk.get_current_scope().iter_trace_propagation_headers()
                    ):
                        logger.debug(
                            f"[Tracing] Adding `{key}` header {value} to outgoing request to {request.url}."
                        )

                        if key == BAGGAGE_HEADER_NAME:
                            add_sentry_baggage_to_headers(request.headers, value)
                        else:
                            request.headers[key] = value

                try:
                    rv = await real_send(self, request, **kwargs)

                    streamed_span.status = "error" if rv.status_code >= 400 else "ok"
                    attributes["http.response.status_code"] = rv.status_code
                finally:
                    streamed_span.set_attributes(attributes)

                # Needs to happen within the context manager as we want to attach the
                # final data before the span finishes and is sent for ingesting.
                with capture_internal_exceptions():
                    add_http_request_source(streamed_span)
        else:
            with sentry_sdk.start_span(
                op=OP.HTTP_CLIENT,
                name="%s %s"
                % (
                    request.method,
                    parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                ),
                origin=HttpxIntegration.origin,
            ) as span:
                span.set_data(SPANDATA.HTTP_METHOD, request.method)
                if parsed_url is not None:
                    span.set_data("url", parsed_url.url)
                    span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                    span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

                if should_propagate_trace(client, str(request.url)):
                    for (
                        key,
                        value,
                    ) in (
                        sentry_sdk.get_current_scope().iter_trace_propagation_headers()
                    ):
                        logger.debug(
                            f"[Tracing] Adding `{key}` header {value} to outgoing request to {request.url}."
                        )
                        if key == BAGGAGE_HEADER_NAME:
                            add_sentry_baggage_to_headers(request.headers, value)
                        else:
                            request.headers[key] = value

                rv = await real_send(self, request, **kwargs)

                span.set_http_status(rv.status_code)
                span.set_data("reason", rv.reason_phrase)

            with capture_internal_exceptions():
                add_http_request_source(span)

        return rv

    AsyncClient.send = send  # type: ignore

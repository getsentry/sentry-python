from __future__ import annotations
import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, BAGGAGE_HEADER_NAME
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing_utils import Baggage, should_propagate_trace
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    ensure_integration_enabled,
    http_client_status_to_breadcrumb_level,
    logger,
    parse_url,
    set_thread_info_from_span,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from typing import Any


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

    @ensure_integration_enabled(HttpxIntegration, real_send)
    def send(self: Client, request: Request, **kwargs: Any) -> Response:
        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        with sentry_sdk.start_span(
            op=OP.HTTP_CLIENT,
            name="%s %s"
            % (
                request.method,
                parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
            ),
            origin=HttpxIntegration.origin,
            only_as_child_span=True,
        ) as span:
            data = {
                SPANDATA.HTTP_METHOD: request.method,
            }
            set_thread_info_from_span(data, span)

            if parsed_url is not None:
                data["url"] = parsed_url.url
                data[SPANDATA.HTTP_QUERY] = parsed_url.query
                data[SPANDATA.HTTP_FRAGMENT] = parsed_url.fragment

            for key, value in data.items():
                span.set_attribute(key, value)

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
                        _add_sentry_baggage_to_headers(request.headers, value)
                    else:
                        request.headers[key] = value

            rv = real_send(self, request, **kwargs)

            span.set_http_status(rv.status_code)
            span.set_attribute("reason", rv.reason_phrase)

            data[SPANDATA.HTTP_STATUS_CODE] = rv.status_code
            data["reason"] = rv.reason_phrase

            sentry_sdk.add_breadcrumb(
                type="http",
                category="httplib",
                data=data,
                level=http_client_status_to_breadcrumb_level(rv.status_code),
            )

            return rv

    Client.send = send


def _install_httpx_async_client() -> None:
    real_send = AsyncClient.send

    async def send(self: AsyncClient, request: Request, **kwargs: Any) -> Response:
        if sentry_sdk.get_client().get_integration(HttpxIntegration) is None:
            return await real_send(self, request, **kwargs)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        with sentry_sdk.start_span(
            op=OP.HTTP_CLIENT,
            name="%s %s"
            % (
                request.method,
                parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
            ),
            origin=HttpxIntegration.origin,
            only_as_child_span=True,
        ) as span:
            data = {
                SPANDATA.HTTP_METHOD: request.method,
            }
            if parsed_url is not None:
                data["url"] = parsed_url.url
                data[SPANDATA.HTTP_QUERY] = parsed_url.query
                data[SPANDATA.HTTP_FRAGMENT] = parsed_url.fragment

            for key, value in data.items():
                span.set_attribute(key, value)

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
                    if key == BAGGAGE_HEADER_NAME and request.headers.get(
                        BAGGAGE_HEADER_NAME
                    ):
                        # do not overwrite any existing baggage, just append to it
                        request.headers[key] += "," + value
                    else:
                        request.headers[key] = value

            rv = await real_send(self, request, **kwargs)

            span.set_http_status(rv.status_code)
            span.set_attribute("reason", rv.reason_phrase)

            data[SPANDATA.HTTP_STATUS_CODE] = rv.status_code
            data["reason"] = rv.reason_phrase

            sentry_sdk.add_breadcrumb(
                type="http",
                category="httplib",
                data=data,
                level=http_client_status_to_breadcrumb_level(rv.status_code),
            )

            return rv

    AsyncClient.send = send


def _add_sentry_baggage_to_headers(
    headers: MutableMapping[str, str], sentry_baggage: str
) -> None:
    """Add the Sentry baggage to the headers.

    This function directly mutates the provided headers. The provided sentry_baggage
    is appended to the existing baggage. If the baggage already contains Sentry items,
    they are stripped out first.
    """
    existing_baggage = headers.get(BAGGAGE_HEADER_NAME, "")
    stripped_existing_baggage = Baggage.strip_sentry_baggage(existing_baggage)

    separator = "," if len(stripped_existing_baggage) > 0 else ""

    headers[BAGGAGE_HEADER_NAME] = (
        stripped_existing_baggage + separator + sentry_baggage
    )

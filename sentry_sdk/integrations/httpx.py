import json

try:
    # py3
    from urllib.parse import parse_qsl
except ImportError:
    # py2
    from urlparse import parse_qsl  # type: ignore

try:
    # py3
    from json import JSONDecodeError
except ImportError:
    # py2 doesn't throw a specialized json error, just Value/TypeErrors
    JSONDecodeError = ValueError  # type: ignore

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME
from sentry_sdk.tracing_utils import should_propagate_trace
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    SentryGraphQLClientError,
    capture_internal_exceptions,
    event_from_exception,
    logger,
    parse_url,
    _get_graphql_operation_name,
    _get_graphql_operation_type,
)
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.integrations._wsgi_common import _filter_headers

if TYPE_CHECKING:
    from typing import Any, Dict, Tuple
    from sentry_sdk._types import EventProcessor


try:
    from httpx import AsyncClient, Client, Request, Response  # type: ignore
except ImportError:
    raise DidNotEnable("httpx is not installed")

__all__ = ["HttpxIntegration"]


class HttpxIntegration(Integration):
    identifier = "httpx"

    def __init__(self, capture_graphql_errors=True):
        # type: (bool) -> None
        self.capture_graphql_errors = capture_graphql_errors

    @staticmethod
    def setup_once():
        # type: () -> None
        """
        httpx has its own transport layer and can be customized when needed,
        so patch Client.send and AsyncClient.send to support both synchronous and async interfaces.
        """
        _install_httpx_client()
        _install_httpx_async_client()


def _install_httpx_client():
    # type: () -> None
    real_send = Client.send

    def send(self, request, **kwargs):
        # type: (Client, Request, **Any) -> Response
        hub = Hub.current
        integration = hub.get_integration(HttpxIntegration)
        if integration is None:
            return real_send(self, request, **kwargs)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        with hub.start_span(
            op=OP.HTTP_CLIENT,
            description="%s %s"
            % (
                request.method,
                parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
            ),
        ) as span:
            span.set_data(SPANDATA.HTTP_METHOD, request.method)
            if parsed_url is not None:
                span.set_data("url", parsed_url.url)
                span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

            if should_propagate_trace(hub, str(request.url)):
                for key, value in hub.iter_trace_propagation_headers():
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

            rv = real_send(self, request, **kwargs)

            span.set_http_status(rv.status_code)
            span.set_data("reason", rv.reason_phrase)

            if integration.capture_graphql_errors:
                _capture_graphql_errors(hub, request, rv)

            return rv

    Client.send = send


def _install_httpx_async_client():
    # type: () -> None
    real_send = AsyncClient.send

    async def send(self, request, **kwargs):
        # type: (AsyncClient, Request, **Any) -> Response
        hub = Hub.current
        integration = hub.get_integration(HttpxIntegration)
        if integration is None:
            return await real_send(self, request, **kwargs)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        with hub.start_span(
            op=OP.HTTP_CLIENT,
            description="%s %s"
            % (
                request.method,
                parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
            ),
        ) as span:
            span.set_data(SPANDATA.HTTP_METHOD, request.method)
            if parsed_url is not None:
                span.set_data("url", parsed_url.url)
                span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

            if should_propagate_trace(hub, str(request.url)):
                for key, value in hub.iter_trace_propagation_headers():
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
            span.set_data("reason", rv.reason_phrase)

            if integration.capture_graphql_errors:
                _capture_graphql_errors(hub, request, rv)

            return rv

    AsyncClient.send = send


def _make_request_processor(request, response):
    # type: (Request, Response) -> EventProcessor
    def httpx_processor(
        event,  # type: Dict[str, Any]
        hint,  # type: Dict[str, Tuple[type, BaseException, Any]]
    ):
        # type: (...) -> Dict[str, Any]
        with capture_internal_exceptions():
            request_info = event.setdefault("request", {})

            parsed_url = parse_url(str(request.url), sanitize=False)
            request_info["url"] = parsed_url.url
            request_info["method"] = request.method
            request_info["headers"] = _filter_headers(dict(request.headers))

            if _should_send_default_pii():
                request_info["query_string"] = parsed_url.query

                request_content = request.read()
                if request_content:
                    try:
                        request_info["data"] = json.loads(request_content)
                    except (JSONDecodeError, TypeError):
                        pass

                if response:
                    response_content = response.json()
                    contexts = event.setdefault("contexts", {})
                    response_context = contexts.setdefault("response", {})
                    response_context["data"] = response_content

            if request.url.path == "/graphql":
                request_info["api_target"] = "graphql"

                query = request_info.get("data")
                if request.method == "GET":
                    query = dict(parse_qsl(parsed_url.query))

                if query:
                    operation_name = _get_graphql_operation_name(query)
                    operation_type = _get_graphql_operation_type(query)
                    event["fingerprint"] = [operation_name, operation_type, 200]
                    event["exception"]["values"][0][
                        "value"
                    ] = "GraphQL request failed, name: {}, type: {}".format(
                        operation_name, operation_type
                    )

        return event

    return httpx_processor


def _capture_graphql_errors(hub, request, response):
    # type: (Hub, Request, Response) -> None
    if (
        request.url.path == "/graphql"
        and request.method in ("GET", "POST")
        and response.status_code == 200
    ):
        with hub.configure_scope() as scope:
            scope.add_event_processor(_make_request_processor(request, response))

            with capture_internal_exceptions():
                try:
                    response_content = response.json()
                except JSONDecodeError:
                    return

                if isinstance(response_content, dict) and response_content.get(
                    "errors"
                ):
                    try:
                        raise SentryGraphQLClientError
                    except SentryGraphQLClientError as ex:
                        event, hint = event_from_exception(
                            ex,
                            client_options=hub.client.options if hub.client else None,
                            mechanism={
                                "type": HttpxIntegration.identifier,
                                "handled": False,
                            },
                        )
                    hub.capture_event(event, hint=hint)

import sys
import weakref
from functools import wraps

import sentry_sdk
from sentry_sdk.api import continue_trace
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations import (
    _DEFAULT_FAILED_REQUEST_STATUS_CODES,
    DidNotEnable,
    Integration,
    _check_minimum_version,
)
from sentry_sdk.integrations._wsgi_common import (
    _filter_headers,
    request_body_within_bounds,
)
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.sessions import track_session
from sentry_sdk.traces import (
    SOURCE_FOR_STYLE as SEGMENT_SOURCE_FOR_STYLE,
)
from sentry_sdk.traces import (
    NoOpStreamedSpan,
    SegmentSource,
    SpanStatus,
    StreamedSpan,
)
from sentry_sdk.tracing import (
    BAGGAGE_HEADER_NAME,
    SOURCE_FOR_STYLE,
    TransactionSource,
)
from sentry_sdk.tracing_utils import (
    add_http_request_source,
    has_span_streaming_enabled,
    should_propagate_trace,
)
from sentry_sdk.utils import (
    CONTEXTVARS_ERROR_MESSAGE,
    HAS_REAL_CONTEXTVARS,
    SENSITIVE_DATA_SUBSTITUTE,
    AnnotatedValue,
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    logger,
    parse_url,
    parse_version,
    reraise,
    transaction_from_function,
)

try:
    import asyncio

    from aiohttp import ClientSession, TraceConfig
    from aiohttp import __version__ as AIOHTTP_VERSION
    from aiohttp.web import Application, HTTPException, UrlDispatcher
except ImportError:
    raise DidNotEnable("AIOHTTP not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Set
    from types import SimpleNamespace
    from typing import Any, ContextManager, Optional, Tuple, Union

    from aiohttp import TraceRequestEndParams, TraceRequestStartParams
    from aiohttp.web_request import Request
    from aiohttp.web_urldispatcher import UrlMappingMatchInfo

    from sentry_sdk._types import Attributes, Event, EventProcessor
    from sentry_sdk.tracing import Span
    from sentry_sdk.utils import ExcInfo


TRANSACTION_STYLE_VALUES = ("handler_name", "method_and_path_pattern")


class AioHttpIntegration(Integration):
    identifier = "aiohttp"
    origin = f"auto.http.{identifier}"

    def __init__(
        self,
        transaction_style: str = "handler_name",
        *,
        failed_request_status_codes: "Set[int]" = _DEFAULT_FAILED_REQUEST_STATUS_CODES,
    ) -> None:
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style
        self._failed_request_status_codes = failed_request_status_codes

    @staticmethod
    def setup_once() -> None:
        version = parse_version(AIOHTTP_VERSION)
        _check_minimum_version(AioHttpIntegration, version)

        if not HAS_REAL_CONTEXTVARS:
            # We better have contextvars or we're going to leak state between
            # requests.
            raise DidNotEnable(
                "The aiohttp integration for Sentry requires Python 3.7+ "
                " or aiocontextvars package." + CONTEXTVARS_ERROR_MESSAGE
            )

        ignore_logger("aiohttp.server")

        old_handle = Application._handle

        async def sentry_app_handle(
            self: "Any", request: "Request", *args: "Any", **kwargs: "Any"
        ) -> "Any":
            client = sentry_sdk.get_client()
            integration = client.get_integration(AioHttpIntegration)
            if integration is None:
                return await old_handle(self, request, *args, **kwargs)

            weak_request = weakref.ref(request)
            is_span_streaming_enabled = has_span_streaming_enabled(client.options)

            with sentry_sdk.isolation_scope() as scope:
                with track_session(scope, session_mode="request"):
                    # Scope data will not leak between requests because aiohttp
                    # create a task to wrap each request.
                    scope.generate_propagation_context()
                    scope.clear_breadcrumbs()
                    scope.add_event_processor(_make_request_processor(weak_request))

                    headers = dict(request.headers)

                    span_ctx: "ContextManager[Union[Span, StreamedSpan]]"
                    if is_span_streaming_enabled:
                        sentry_sdk.traces.continue_trace(headers)
                        sentry_sdk.get_current_scope().set_custom_sampling_context(
                            {"aiohttp_request": request}
                        )

                        header_attributes: "dict[str, Any]" = {}
                        for header, header_value in _filter_headers(
                            headers, use_annotated_value=False
                        ).items():
                            header_attributes[
                                f"http.request.header.{header.lower()}"
                            ] = (
                                # header_value will always be a string because we set `use_annotated_value` to false above
                                header_value
                            )

                        url_query_attribute = (
                            {"url.query": request.query_string}
                            if request.query_string
                            else {}
                        )

                        client_address_attributes = (
                            {
                                "client.address": request.remote,
                                "user.ip_address": request.remote,
                            }
                            if should_send_default_pii() and request.remote
                            else {}
                        )

                        span_ctx = sentry_sdk.traces.start_span(
                            # If this name makes it to the UI, AIOHTTP's URL
                            # resolver did not find a route or died trying.
                            name="generic AIOHTTP request",
                            attributes={
                                "sentry.op": OP.HTTP_SERVER,
                                "sentry.origin": AioHttpIntegration.origin,
                                "sentry.span.source": SegmentSource.ROUTE.value,
                                "url.full": "%s://%s%s"
                                % (request.scheme, request.host, request.path),
                                "http.request.method": request.method,
                                **url_query_attribute,
                                **client_address_attributes,
                                **header_attributes,
                            },
                        )
                    else:
                        transaction = continue_trace(
                            headers,
                            op=OP.HTTP_SERVER,
                            # If this transaction name makes it to the UI, AIOHTTP's
                            # URL resolver did not find a route or died trying.
                            name="generic AIOHTTP request",
                            source=TransactionSource.ROUTE,
                            origin=AioHttpIntegration.origin,
                        )
                        span_ctx = sentry_sdk.start_transaction(
                            transaction,
                            custom_sampling_context={"aiohttp_request": request},
                        )

                    with span_ctx as span:
                        try:
                            try:
                                response = await old_handle(self, request)
                            except HTTPException as e:
                                if isinstance(span, StreamedSpan) and not isinstance(
                                    span, NoOpStreamedSpan
                                ):
                                    span.set_attribute(
                                        "http.response.status_code", e.status_code
                                    )

                                    if e.status_code >= 400:
                                        span.status = SpanStatus.ERROR.value
                                    else:
                                        span.status = SpanStatus.OK.value
                                else:
                                    # Since a NoOpStreamedSpan can end up here, we have to guard against it
                                    # so this only gets set in the legacy transaction approach.
                                    if not isinstance(span, NoOpStreamedSpan):
                                        span.set_http_status(e.status_code)

                                if (
                                    e.status_code
                                    in integration._failed_request_status_codes
                                ):
                                    _capture_exception()
                                raise
                            except (asyncio.CancelledError, ConnectionResetError):
                                if isinstance(span, StreamedSpan):
                                    span.status = SpanStatus.ERROR.value
                                else:
                                    span.set_status(SPANSTATUS.CANCELLED)
                                raise
                            except Exception:
                                # This will probably map to a 500 but seems like we
                                # have no way to tell. Do not set span status.
                                reraise(*_capture_exception())
                        finally:
                            # The handler has had a chance to read the body, so
                            # request._read_bytes may now be populated. Capture
                            # body data on the segment regardless of outcome.
                            if isinstance(span, StreamedSpan) and not isinstance(
                                span, NoOpStreamedSpan
                            ):
                                with capture_internal_exceptions():
                                    raw_data = get_aiohttp_request_data(request)
                                    body_data = (
                                        raw_data.value
                                        if isinstance(raw_data, AnnotatedValue)
                                        else raw_data
                                    )
                                    if body_data is not None:
                                        span._segment.set_attribute(
                                            "http.request.body.data", body_data
                                        )

                        try:
                            # A valid response handler will return a valid response with a status. But, if the handler
                            # returns an invalid response (e.g. None), the line below will raise an AttributeError.
                            # Even though this is likely invalid, we need to handle this case to ensure we don't break
                            # the application.
                            response_status = response.status
                        except AttributeError:
                            pass
                        else:
                            if isinstance(span, StreamedSpan):
                                span.set_attribute(
                                    "http.response.status_code", response_status
                                )
                                span.status = (
                                    SpanStatus.ERROR.value
                                    if response_status >= 400
                                    else SpanStatus.OK.value
                                )
                            else:
                                span.set_http_status(response_status)

                        return response

        Application._handle = sentry_app_handle

        old_urldispatcher_resolve = UrlDispatcher.resolve

        @wraps(old_urldispatcher_resolve)
        async def sentry_urldispatcher_resolve(
            self: "UrlDispatcher", request: "Request"
        ) -> "UrlMappingMatchInfo":
            rv = await old_urldispatcher_resolve(self, request)

            integration = sentry_sdk.get_client().get_integration(AioHttpIntegration)
            if integration is None:
                return rv

            name = None

            try:
                if integration.transaction_style == "handler_name":
                    name = transaction_from_function(rv.handler)
                elif integration.transaction_style == "method_and_path_pattern":
                    route_info = rv.get_info()
                    pattern = route_info.get("path") or route_info.get("formatter")
                    name = "{} {}".format(request.method, pattern)
            except Exception:
                pass

            if name is not None:
                current_span = sentry_sdk.get_current_span()
                if isinstance(current_span, StreamedSpan) and not isinstance(
                    current_span, NoOpStreamedSpan
                ):
                    current_span._segment.name = name
                    current_span._segment.set_attribute(
                        "sentry.span.source",
                        SEGMENT_SOURCE_FOR_STYLE[integration.transaction_style].value,
                    )
                else:
                    current_scope = sentry_sdk.get_current_scope()
                    current_scope.set_transaction_name(
                        name,
                        source=SOURCE_FOR_STYLE[integration.transaction_style],
                    )

            return rv

        UrlDispatcher.resolve = sentry_urldispatcher_resolve

        old_client_session_init = ClientSession.__init__

        @ensure_integration_enabled(AioHttpIntegration, old_client_session_init)
        def init(*args: "Any", **kwargs: "Any") -> None:
            client_trace_configs = list(kwargs.get("trace_configs") or ())
            trace_config = create_trace_config()
            client_trace_configs.append(trace_config)

            kwargs["trace_configs"] = client_trace_configs
            return old_client_session_init(*args, **kwargs)

        ClientSession.__init__ = init


def create_trace_config() -> "TraceConfig":
    async def on_request_start(
        session: "ClientSession",
        trace_config_ctx: "SimpleNamespace",
        params: "TraceRequestStartParams",
    ) -> None:
        client = sentry_sdk.get_client()
        if client.get_integration(AioHttpIntegration) is None:
            return

        method = params.method.upper()

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(params.url), sanitize=False)

        span_name = "%s %s" % (
            method,
            parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
        )

        span: "Union[Span, StreamedSpan]"
        if has_span_streaming_enabled(client.options):
            attributes: "Attributes" = {
                "sentry.op": OP.HTTP_CLIENT,
                "sentry.origin": AioHttpIntegration.origin,
                "http.request.method": method,
            }
            if parsed_url is not None:
                attributes["url.full"] = parsed_url.url
                if parsed_url.query:
                    attributes["url.query"] = parsed_url.query
                if parsed_url.fragment:
                    attributes["url.fragment"] = parsed_url.fragment

            span = sentry_sdk.traces.start_span(name=span_name, attributes=attributes)
        else:
            legacy_span = sentry_sdk.start_span(
                op=OP.HTTP_CLIENT,
                name=span_name,
                origin=AioHttpIntegration.origin,
            )
            legacy_span.set_data(SPANDATA.HTTP_METHOD, method)
            if parsed_url is not None:
                legacy_span.set_data("url", parsed_url.url)
                legacy_span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                legacy_span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)
            span = legacy_span

        if should_propagate_trace(client, str(params.url)):
            for (
                key,
                value,
            ) in sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                span=span
            ):
                logger.debug(
                    "[Tracing] Adding `{key}` header {value} to outgoing request to {url}.".format(
                        key=key, value=value, url=params.url
                    )
                )
                if key == BAGGAGE_HEADER_NAME and params.headers.get(
                    BAGGAGE_HEADER_NAME
                ):
                    # do not overwrite any existing baggage, just append to it
                    params.headers[key] += "," + value
                else:
                    params.headers[key] = value

        trace_config_ctx.span = span

    async def on_request_end(
        session: "ClientSession",
        trace_config_ctx: "SimpleNamespace",
        params: "TraceRequestEndParams",
    ) -> None:
        if trace_config_ctx.span is None:
            return

        span = trace_config_ctx.span
        status = int(params.response.status)

        if isinstance(span, StreamedSpan):
            span.set_attribute("http.response.status_code", status)
            span.status = (
                SpanStatus.ERROR.value if status >= 400 else SpanStatus.OK.value
            )

            with capture_internal_exceptions():
                add_http_request_source(span)
            span.end()
        else:
            span.set_http_status(status)
            span.set_data("reason", params.response.reason)
            span.finish()
            with capture_internal_exceptions():
                add_http_request_source(span)

    trace_config = TraceConfig()

    trace_config.on_request_start.append(on_request_start)
    trace_config.on_request_end.append(on_request_end)

    return trace_config


def _make_request_processor(
    weak_request: "weakref.ReferenceType[Request]",
) -> "EventProcessor":
    def aiohttp_processor(
        event: "Event",
        hint: "dict[str, Tuple[type, BaseException, Any]]",
    ) -> "Event":
        request = weak_request()
        if request is None:
            return event

        with capture_internal_exceptions():
            request_info = event.setdefault("request", {})

            request_info["url"] = "%s://%s%s" % (
                request.scheme,
                request.host,
                request.path,
            )

            request_info["query_string"] = request.query_string
            request_info["method"] = request.method
            request_info["env"] = {"REMOTE_ADDR": request.remote}
            request_info["headers"] = _filter_headers(dict(request.headers))

            # Just attach raw data here if it is within bounds, if available.
            # Unfortunately there's no way to get structured data from aiohttp
            # without awaiting on some coroutine.
            request_info["data"] = get_aiohttp_request_data(request)

        return event

    return aiohttp_processor


def _capture_exception() -> "ExcInfo":
    exc_info = sys.exc_info()
    event, hint = event_from_exception(
        exc_info,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "aiohttp", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)
    return exc_info


BODY_NOT_READ_MESSAGE = "[Can't show request body due to implementation details.]"


def get_aiohttp_request_data(
    request: "Request",
) -> "Union[Optional[str], AnnotatedValue]":
    bytes_body = request._read_bytes

    if bytes_body is not None:
        # we have body to show
        if not request_body_within_bounds(sentry_sdk.get_client(), len(bytes_body)):
            return AnnotatedValue.substituted_because_over_size_limit()

        encoding = request.charset or "utf-8"
        return bytes_body.decode(encoding, "replace")

    if request.can_read_body:
        # body exists but we can't show it
        return BODY_NOT_READ_MESSAGE

    # request has no body
    return None

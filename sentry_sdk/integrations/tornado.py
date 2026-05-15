import contextlib
import weakref
from inspect import iscoroutinefunction

import sentry_sdk
from sentry_sdk.api import continue_trace
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations._wsgi_common import (
    RequestExtractor,
    _filter_headers,
    _is_json_content_type,
    request_body_within_bounds,
)
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import NoOpStreamedSpan, SegmentSource, StreamedSpan
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    CONTEXTVARS_ERROR_MESSAGE,
    HAS_REAL_CONTEXTVARS,
    AnnotatedValue,
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    transaction_from_function,
)

try:
    from tornado import version_info as TORNADO_VERSION
    from tornado.gen import coroutine
    from tornado.web import HTTPError, RequestHandler
except ImportError:
    raise DidNotEnable("Tornado not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, ContextManager, Dict, Generator, Optional, Union

    from sentry_sdk._types import Event, EventProcessor
    from sentry_sdk.tracing import Span


class TornadoIntegration(Integration):
    identifier = "tornado"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        _check_minimum_version(TornadoIntegration, TORNADO_VERSION)

        if not HAS_REAL_CONTEXTVARS:
            # Tornado is async. We better have contextvars or we're going to leak
            # state between requests.
            raise DidNotEnable(
                "The tornado integration for Sentry requires Python 3.7+ or the aiocontextvars package"
                + CONTEXTVARS_ERROR_MESSAGE
            )

        ignore_logger("tornado.access")

        old_execute = RequestHandler._execute

        awaitable = iscoroutinefunction(old_execute)

        if awaitable:
            # Starting Tornado 6 RequestHandler._execute method is a standard Python coroutine (async/await)
            # In that case our method should be a coroutine function too
            async def sentry_execute_request_handler(
                self: "RequestHandler", *args: "Any", **kwargs: "Any"
            ) -> "Any":
                with _handle_request_impl(self):
                    return await old_execute(self, *args, **kwargs)

        else:

            @coroutine  # type: ignore
            def sentry_execute_request_handler(
                self: "RequestHandler", *args: "Any", **kwargs: "Any"
            ) -> "Any":
                with _handle_request_impl(self):
                    result = yield from old_execute(self, *args, **kwargs)
                    return result

        RequestHandler._execute = sentry_execute_request_handler

        old_log_exception = RequestHandler.log_exception

        def sentry_log_exception(
            self: "Any",
            ty: type,
            value: BaseException,
            tb: "Any",
            *args: "Any",
            **kwargs: "Any",
        ) -> "Optional[Any]":
            _capture_exception(ty, value, tb)
            return old_log_exception(self, ty, value, tb, *args, **kwargs)

        RequestHandler.log_exception = sentry_log_exception


_DEFAULT_TRANSACTION_NAME = "generic Tornado request"


@contextlib.contextmanager
def _handle_request_impl(self: "RequestHandler") -> "Generator[None, None, None]":
    integration = sentry_sdk.get_client().get_integration(TornadoIntegration)

    if integration is None:
        yield
        return

    weak_handler = weakref.ref(self)
    client = sentry_sdk.get_client()
    is_span_streaming_enabled = has_span_streaming_enabled(client.options)

    with sentry_sdk.isolation_scope() as scope:
        headers = self.request.headers

        scope.clear_breadcrumbs()
        processor = _make_event_processor(weak_handler)
        scope.add_event_processor(processor)

        span_ctx: "ContextManager[Union[Span, StreamedSpan, None]]"

        if is_span_streaming_enabled:
            sentry_sdk.traces.continue_trace(dict(headers))
            scope.set_custom_sampling_context({"tornado_request": self.request})

            span_ctx = sentry_sdk.traces.start_span(
                name=_DEFAULT_TRANSACTION_NAME,
                attributes={
                    "sentry.op": OP.HTTP_SERVER,
                    "sentry.origin": TornadoIntegration.origin,
                    "sentry.span.source": SegmentSource.ROUTE,
                },
                parent_span=None,
            )
        else:
            transaction = continue_trace(
                headers,
                op=OP.HTTP_SERVER,
                # Like with all other integrations, this is our
                # fallback transaction in case there is no route.
                # sentry_urldispatcher_resolve is responsible for
                # setting a transaction name later.
                name=_DEFAULT_TRANSACTION_NAME,
                source=TransactionSource.ROUTE,
                origin=TornadoIntegration.origin,
            )
            span_ctx = sentry_sdk.start_transaction(
                transaction,
                custom_sampling_context={"tornado_request": self.request},
            )

        with span_ctx as span:
            if isinstance(span, StreamedSpan) and not isinstance(
                span, NoOpStreamedSpan
            ):
                with capture_internal_exceptions():
                    for attr, value in _get_request_attributes(self.request).items():
                        span.set_attribute(attr, value)

                    method = getattr(self, self.request.method.lower(), None)
                    if method is not None:
                        span_name = transaction_from_function(method)
                        if span_name:
                            span.name = span_name
                            span.set_attribute(
                                "sentry.span.source",
                                SegmentSource.COMPONENT,
                            )

            try:
                yield
            finally:
                if isinstance(span, StreamedSpan) and not isinstance(
                    span, NoOpStreamedSpan
                ):
                    with capture_internal_exceptions():
                        status_int = self.get_status()
                        span.set_attribute(SPANDATA.HTTP_STATUS_CODE, status_int)
                        span.status = "error" if status_int >= 400 else "ok"


def _get_request_attributes(request: "Any") -> "Dict[str, Any]":
    attributes = {}  # type: Dict[str, Any]

    if request.method:
        attributes[SPANDATA.HTTP_REQUEST_METHOD] = request.method.upper()

    headers = _filter_headers(dict(request.headers), use_annotated_value=False)
    for header, value in headers.items():
        attributes[f"{SPANDATA.HTTP_REQUEST_HEADER}.{header.lower()}"] = value

    if request.query:
        attributes[SPANDATA.HTTP_QUERY] = request.query

    attributes[SPANDATA.URL_FULL] = "%s://%s%s" % (
        request.protocol,
        request.host,
        request.path,
    )

    if request.protocol:
        attributes[SPANDATA.NETWORK_PROTOCOL_NAME] = request.protocol

    if should_send_default_pii() and request.remote_ip:
        attributes[SPANDATA.CLIENT_ADDRESS] = request.remote_ip
        attributes[SPANDATA.USER_IP_ADDRESS] = request.remote_ip

    with capture_internal_exceptions():
        raw_data = _get_tornado_request_data(request)
        body_data = raw_data.value if isinstance(raw_data, AnnotatedValue) else raw_data
        if body_data is not None:
            attributes[SPANDATA.HTTP_REQUEST_BODY_DATA] = body_data

    return attributes


def _get_tornado_request_data(
    request: "Any",
) -> "Union[Optional[str], AnnotatedValue]":
    body = request.body
    if not body:
        return None

    if not request_body_within_bounds(sentry_sdk.get_client(), len(body)):
        return AnnotatedValue.substituted_because_over_size_limit()

    return body.decode("utf-8", "replace")


@ensure_integration_enabled(TornadoIntegration)
def _capture_exception(ty: type, value: BaseException, tb: "Any") -> None:
    if isinstance(value, HTTPError):
        return

    event, hint = event_from_exception(
        (ty, value, tb),
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "tornado", "handled": False},
    )

    sentry_sdk.capture_event(event, hint=hint)


def _make_event_processor(
    weak_handler: "Callable[[], RequestHandler]",
) -> "EventProcessor":
    def tornado_processor(event: "Event", hint: "dict[str, Any]") -> "Event":
        handler = weak_handler()
        if handler is None:
            return event

        request = handler.request

        with capture_internal_exceptions():
            method = getattr(handler, handler.request.method.lower())
            event["transaction"] = transaction_from_function(method) or ""
            event["transaction_info"] = {"source": TransactionSource.COMPONENT}

        with capture_internal_exceptions():
            extractor = TornadoRequestExtractor(request)
            extractor.extract_into_event(event)

            request_info = event["request"]

            request_info["url"] = "%s://%s%s" % (
                request.protocol,
                request.host,
                request.path,
            )

            request_info["query_string"] = request.query
            request_info["method"] = request.method
            request_info["env"] = {"REMOTE_ADDR": request.remote_ip}
            request_info["headers"] = _filter_headers(dict(request.headers))

        if should_send_default_pii():
            try:
                current_user = handler.current_user
            except Exception:
                current_user = None

            if current_user:
                event.setdefault("user", {}).setdefault("is_authenticated", True)

        return event

    return tornado_processor


class TornadoRequestExtractor(RequestExtractor):
    def content_length(self) -> int:
        if self.request.body is None:
            return 0
        return len(self.request.body)

    def cookies(self) -> "Dict[str, str]":
        return {k: v.value for k, v in self.request.cookies.items()}

    def raw_data(self) -> bytes:
        return self.request.body

    def form(self) -> "Dict[str, Any]":
        return {
            k: [v.decode("latin1", "replace") for v in vs]
            for k, vs in self.request.body_arguments.items()
        }

    def is_json(self) -> bool:
        return _is_json_content_type(self.request.headers.get("content-type"))

    def files(self) -> "Dict[str, Any]":
        return {k: v[0] for k, v in self.request.files.items() if v}

    def size_of_file(self, file: "Any") -> int:
        return len(file.body or ())

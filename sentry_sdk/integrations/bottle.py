import functools
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import (
    _DEFAULT_FAILED_REQUEST_STATUS_CODES,
    DidNotEnable,
    Integration,
    _check_minimum_version,
)
from sentry_sdk.integrations._wsgi_common import RequestExtractor
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.traces import SegmentNameSource
from sentry_sdk.utils import (
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    parse_version,
)

if TYPE_CHECKING:
    from collections.abc import Set
    from typing import Any, Callable, Dict, Optional

    from bottle import FileUpload, FormsDict, LocalRequest  # type: ignore

    from sentry_sdk._types import Event, EventProcessor
    from sentry_sdk.integrations.wsgi import _ScopedResponse

try:
    from bottle import (
        Bottle,
        HTTPResponse,
        Route,
    )
    from bottle import (
        __version__ as BOTTLE_VERSION,
    )
    from bottle import (
        request as bottle_request,
    )
except ImportError:
    raise DidNotEnable("Bottle not installed or incompatible")


class BottleIntegration(Integration):
    identifier = "bottle"
    origin = f"auto.http.{identifier}"

    def __init__(
        self,
        *,
        failed_request_status_codes: "Set[int]" = _DEFAULT_FAILED_REQUEST_STATUS_CODES,
    ) -> None:
        self.failed_request_status_codes = failed_request_status_codes

    @staticmethod
    def setup_once() -> None:
        version = parse_version(BOTTLE_VERSION)
        _check_minimum_version(BottleIntegration, version)

        # Bottle's Route.__repr__ might lead to a never-terminating while True
        # loop when attach_stacktrace=True.
        def _sentry_route_repr(self: "Route") -> str:
            cb = self.callback
            return "<%s %s -> %s:%s>" % (
                self.method,
                self.rule,
                getattr(cb, "__module__", "?"),
                getattr(cb, "__name__", "?"),
            )

        Route.__sentry_repr__ = _sentry_route_repr

        old_app = Bottle.__call__

        @ensure_integration_enabled(BottleIntegration, old_app)
        def sentry_patched_wsgi_app(
            self: "Any", environ: "Dict[str, str]", start_response: "Callable[..., Any]"
        ) -> "_ScopedResponse":
            middleware = SentryWsgiMiddleware(
                lambda *a, **kw: old_app(self, *a, **kw),
                span_origin=BottleIntegration.origin,
            )

            return middleware(environ, start_response)

        Bottle.__call__ = sentry_patched_wsgi_app

        old_handle = Bottle._handle

        @functools.wraps(old_handle)
        def _patched_handle(self: "Bottle", environ: "Dict[str, Any]") -> "Any":
            integration = sentry_sdk.get_client().get_integration(BottleIntegration)
            if integration is None:
                return old_handle(self, environ)

            scope = sentry_sdk.get_isolation_scope()
            scope._name = "bottle"
            scope.add_event_processor(
                _make_request_event_processor(self, bottle_request, integration)
            )
            res = old_handle(self, environ)

            server_span = sentry_sdk.get_current_scope()._server_segment_span
            if server_span is not None:
                route_path = None
                try:
                    route_path = bottle_request.route.rule
                except RuntimeError:
                    pass

                if route_path is not None:
                    server_span.set_attribute(SPANDATA.HTTP_ROUTE, route_path)

            name = _get_transaction_name(bottle_request)
            if name:
                sentry_sdk.get_current_scope().set_transaction_name(
                    name,
                    source=SegmentNameSource.ROUTE,
                )

            return res

        Bottle._handle = _patched_handle

        old_make_callback = Route._make_callback

        @functools.wraps(old_make_callback)
        def patched_make_callback(
            self: "Route", *args: object, **kwargs: object
        ) -> "Any":
            prepared_callback = old_make_callback(self, *args, **kwargs)

            integration = sentry_sdk.get_client().get_integration(BottleIntegration)
            if integration is None:
                return prepared_callback

            def wrapped_callback(*args: object, **kwargs: object) -> "Any":
                try:
                    res = prepared_callback(*args, **kwargs)
                except Exception as exception:
                    _capture_exception(exception, handled=False)
                    raise exception

                if (
                    isinstance(res, HTTPResponse)
                    and res.status_code in integration.failed_request_status_codes
                ):
                    _capture_exception(res, handled=True)

                return res

            return wrapped_callback

        Route._make_callback = patched_make_callback


class BottleRequestExtractor(RequestExtractor):
    def env(self) -> "Dict[str, str]":
        return self.request.environ

    def cookies(self) -> "Dict[str, str]":
        return self.request.cookies

    def raw_data(self) -> bytes:
        return self.request.body.read()

    def form(self) -> "FormsDict":
        if self.is_json():
            return None
        return self.request.forms.decode()

    def files(self) -> "Optional[Dict[str, str]]":
        if self.is_json():
            return None

        return self.request.files

    def size_of_file(self, file: "FileUpload") -> int:
        return file.content_length


def _get_transaction_name(request: "Any") -> str:
    try:
        return request.route.rule or "bottle request"
    except RuntimeError:
        return "bottle request"


def _make_request_event_processor(
    app: "Bottle", request: "LocalRequest", integration: "BottleIntegration"
) -> "EventProcessor":
    def event_processor(event: "Event", hint: "dict[str, Any]") -> "Event":
        name = _get_transaction_name(request)
        event["transaction"] = name
        event["transaction_info"] = {"source": SegmentNameSource.ROUTE}

        with capture_internal_exceptions():
            BottleRequestExtractor(request).extract_into_event(event)

        return event

    return event_processor


def _capture_exception(exception: BaseException, handled: bool) -> None:
    event, hint = event_from_exception(
        exception,
        mechanism={"type": "bottle", "handled": handled},
    )
    sentry_sdk.capture_event(event, hint=hint)

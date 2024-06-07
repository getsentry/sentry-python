import sentry_sdk
from sentry_sdk.tracing import SOURCE_FOR_STYLE
from sentry_sdk.utils import (
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    parse_version,
    transaction_from_function,
)
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.integrations._wsgi_common import RequestExtractor
from sentry_sdk.scope import Scope
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from sentry_sdk.integrations.wsgi import _ScopedResponse
    from typing import Any
    from typing import Dict
    from typing import Callable
    from typing import Optional
    from bottle import FileUpload, FormsDict, LocalRequest  # type: ignore

    from sentry_sdk._types import EventProcessor, Event

try:
    from bottle import (
        Bottle,
        Route,
        request as bottle_request,
        HTTPResponse,
        __version__ as BOTTLE_VERSION,
    )
except ImportError:
    raise DidNotEnable("Bottle not installed")


TRANSACTION_STYLE_VALUES = ("endpoint", "url")


class BottleIntegration(Integration):
    identifier = "bottle"
    origin = f"auto.http.{identifier}"

    transaction_style = ""

    def __init__(self, transaction_style="endpoint"):
        # type: (str) -> None

        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    @staticmethod
    def setup_once():
        # type: () -> None
        version = parse_version(BOTTLE_VERSION)

        if version is None:
            raise DidNotEnable("Unparsable Bottle version: {}".format(BOTTLE_VERSION))

        if version < (0, 12):
            raise DidNotEnable("Bottle 0.12 or newer required.")

        old_app = Bottle.__call__

        @ensure_integration_enabled(BottleIntegration, old_app)
        def sentry_patched_wsgi_app(self, environ, start_response):
            # type: (Any, Dict[str, str], Callable[..., Any]) -> _ScopedResponse
            middleware = SentryWsgiMiddleware(
                lambda *a, **kw: old_app(self, *a, **kw),
                span_origin=BottleIntegration.origin,
            )

            return middleware(environ, start_response)

        Bottle.__call__ = sentry_patched_wsgi_app

        old_handle = Bottle._handle

        @ensure_integration_enabled(BottleIntegration, old_handle)
        def _patched_handle(self, environ):
            # type: (Bottle, Dict[str, Any]) -> Any
            integration = sentry_sdk.get_client().get_integration(BottleIntegration)

            scope = Scope.get_isolation_scope()
            scope._name = "bottle"
            scope.add_event_processor(
                _make_request_event_processor(self, bottle_request, integration)
            )
            res = old_handle(self, environ)

            return res

        Bottle._handle = _patched_handle

        old_make_callback = Route._make_callback

        @ensure_integration_enabled(BottleIntegration, old_make_callback)
        def patched_make_callback(self, *args, **kwargs):
            # type: (Route, *object, **object) -> Any
            client = sentry_sdk.get_client()
            prepared_callback = old_make_callback(self, *args, **kwargs)

            def wrapped_callback(*args, **kwargs):
                # type: (*object, **object) -> Any

                try:
                    res = prepared_callback(*args, **kwargs)
                except HTTPResponse:
                    raise
                except Exception as exception:
                    event, hint = event_from_exception(
                        exception,
                        client_options=client.options,
                        mechanism={"type": "bottle", "handled": False},
                    )
                    sentry_sdk.capture_event(event, hint=hint)
                    raise exception

                return res

            return wrapped_callback

        Route._make_callback = patched_make_callback


class BottleRequestExtractor(RequestExtractor):
    def env(self):
        # type: () -> Dict[str, str]
        return self.request.environ

    def cookies(self):
        # type: () -> Dict[str, str]
        return self.request.cookies

    def raw_data(self):
        # type: () -> bytes
        return self.request.body.read()

    def form(self):
        # type: () -> FormsDict
        if self.is_json():
            return None
        return self.request.forms.decode()

    def files(self):
        # type: () -> Optional[Dict[str, str]]
        if self.is_json():
            return None

        return self.request.files

    def size_of_file(self, file):
        # type: (FileUpload) -> int
        return file.content_length


def _set_transaction_name_and_source(event, transaction_style, request):
    # type: (Event, str, Any) -> None
    name = ""

    if transaction_style == "url":
        name = request.route.rule or ""

    elif transaction_style == "endpoint":
        name = (
            request.route.name
            or transaction_from_function(request.route.callback)
            or ""
        )

    event["transaction"] = name
    event["transaction_info"] = {"source": SOURCE_FOR_STYLE[transaction_style]}


def _make_request_event_processor(app, request, integration):
    # type: (Bottle, LocalRequest, BottleIntegration) -> EventProcessor

    def event_processor(event, hint):
        # type: (Event, dict[str, Any]) -> Event
        _set_transaction_name_and_source(event, integration.transaction_style, request)

        with capture_internal_exceptions():
            BottleRequestExtractor(request).extract_into_event(event)

        return event

    return event_processor

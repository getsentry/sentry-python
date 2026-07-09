import asyncio
import inspect
import sys
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import SOURCE_FOR_STYLE as SEGMENT_SOURCE_FOR_STYLE
from sentry_sdk.traces import StreamedSpan, get_current_span
from sentry_sdk.tracing import SOURCE_FOR_STYLE as TRANSACTION_SOURCE_FOR_STYLE
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
)

if TYPE_CHECKING:
    from typing import Any, Union

    from sentry_sdk._types import Event, EventProcessor

try:
    import quart_auth  # type: ignore
except ImportError:
    quart_auth = None

try:
    from quart import (  # type: ignore
        Quart,
        Request,
        has_request_context,
        has_websocket_context,
        request,
        websocket,
    )
    from quart.signals import (  # type: ignore
        got_background_exception,
        got_request_exception,
        got_websocket_exception,
        request_started,
        websocket_started,
    )
except ImportError:
    raise DidNotEnable("Quart is not installed")
else:
    # Quart 0.19 is based on Flask and hence no longer has a Scaffold
    try:
        from quart.scaffold import Scaffold  # type: ignore
    except ImportError:
        from flask.sansio.scaffold import Scaffold  # type: ignore

TRANSACTION_STYLE_VALUES = ("endpoint", "url")


class QuartIntegration(Integration):
    identifier = "quart"
    origin = f"auto.http.{identifier}"

    transaction_style = ""

    def __init__(self, transaction_style: str = "endpoint") -> None:
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    @staticmethod
    def setup_once() -> None:
        request_started.connect(_request_websocket_started)
        websocket_started.connect(_request_websocket_started)
        got_background_exception.connect(_capture_exception)
        got_request_exception.connect(_capture_exception)
        got_websocket_exception.connect(_capture_exception)

        patch_asgi_app()
        patch_scaffold_route()


def patch_asgi_app() -> None:
    old_app = Quart.__call__

    async def sentry_patched_asgi_app(
        self: "Any", scope: "Any", receive: "Any", send: "Any"
    ) -> "Any":
        if sentry_sdk.get_client().get_integration(QuartIntegration) is None:
            return await old_app(self, scope, receive, send)

        middleware = SentryAsgiMiddleware(
            lambda *a, **kw: old_app(self, *a, **kw),
            span_origin=QuartIntegration.origin,
            asgi_version=3,
        )
        return await middleware(scope, receive, send)

    Quart.__call__ = sentry_patched_asgi_app


def patch_scaffold_route() -> None:
    # Vendored: https://github.com/pallets/quart/blob/5817e983d0b586889337a596d674c0c246d68878/src/quart/app.py#L137-L140
    if sys.version_info >= (3, 12):
        iscoroutinefunction = inspect.iscoroutinefunction
    else:
        iscoroutinefunction = asyncio.iscoroutinefunction

    old_route = Scaffold.route

    def _sentry_route(*args: "Any", **kwargs: "Any") -> "Any":
        old_decorator = old_route(*args, **kwargs)

        def decorator(old_func: "Any") -> "Any":
            if inspect.isfunction(old_func) and not iscoroutinefunction(old_func):

                @wraps(old_func)
                @ensure_integration_enabled(QuartIntegration, old_func)
                def _sentry_func(*args: "Any", **kwargs: "Any") -> "Any":
                    client = sentry_sdk.get_client()
                    if has_span_streaming_enabled(client.options):
                        span = get_current_span()
                        if span is not None and hasattr(span, "_segment"):
                            span._segment._update_active_thread()
                    else:
                        current_scope = sentry_sdk.get_current_scope()
                        if current_scope.transaction is not None:
                            current_scope.transaction.update_active_thread()

                    sentry_scope = sentry_sdk.get_isolation_scope()
                    if sentry_scope.profile is not None:
                        sentry_scope.profile.update_active_thread_id()

                    return old_func(*args, **kwargs)

                return old_decorator(_sentry_func)

            return old_decorator(old_func)

        return decorator

    Scaffold.route = _sentry_route


def _set_transaction_name_and_source(
    scope: "sentry_sdk.Scope", transaction_style: str, request: "Request"
) -> None:
    try:
        name_for_style = {
            "url": request.url_rule.rule,
            "endpoint": request.url_rule.endpoint,
        }

        source = (
            SEGMENT_SOURCE_FOR_STYLE[transaction_style]
            if has_span_streaming_enabled(sentry_sdk.get_client().options)
            else TRANSACTION_SOURCE_FOR_STYLE[transaction_style]
        )

        scope.set_transaction_name(
            name=name_for_style[transaction_style],
            source=source,
        )
    except Exception:
        pass


async def _request_websocket_started(app: "Quart", **kwargs: "Any") -> None:
    integration = sentry_sdk.get_client().get_integration(QuartIntegration)
    if integration is None:
        return

    if has_request_context():
        request_websocket = request._get_current_object()
    if has_websocket_context():
        request_websocket = websocket._get_current_object()

    # Set the transaction name here, but rely on ASGI middleware
    # to actually start the transaction
    _set_transaction_name_and_source(
        sentry_sdk.get_current_scope(), integration.transaction_style, request_websocket
    )

    scope = sentry_sdk.get_isolation_scope()

    if has_span_streaming_enabled(sentry_sdk.get_client().options):
        current_span = get_current_span()
        if type(current_span) is StreamedSpan:
            segment = current_span._segment

            segment.set_attribute("http.request.method", request_websocket.method)
            header_attributes: "dict[str, Any]" = {}

            for header, header_value in _filter_headers(
                dict(request_websocket.headers), use_annotated_value=False
            ).items():
                header_attributes[f"http.request.header.{header.lower()}"] = (
                    header_value
                )

            segment.set_attributes(header_attributes)

            if should_send_default_pii():
                segment.set_attribute("url.full", request_websocket.url)
                segment.set_attribute(
                    "url.query",
                    request_websocket.query_string.decode("utf-8", errors="replace"),
                )

                user_properties = {}
                if len(request_websocket.access_route) >= 1:
                    segment.set_attribute(
                        "client.address", request_websocket.access_route[0]
                    )
                    user_properties["ip_address"] = request_websocket.access_route[0]

                current_user_id = _get_current_user_id_from_quart()
                if current_user_id:
                    user_properties["id"] = current_user_id

                if user_properties:
                    existing_user_properties = scope._user or {}
                    scope.set_user({**existing_user_properties, **user_properties})

    evt_processor = _make_request_event_processor(app, request_websocket, integration)
    scope.add_event_processor(evt_processor)


def _make_request_event_processor(
    app: "Quart", request: "Request", integration: "QuartIntegration"
) -> "EventProcessor":
    def inner(event: "Event", hint: "dict[str, Any]") -> "Event":
        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        if request is None:
            return event

        with capture_internal_exceptions():
            # TODO: Figure out what to do with request body. Methods on request
            # are async, but event processors are not.

            request_info = event.setdefault("request", {})
            request_info["url"] = request.url
            request_info["query_string"] = request.query_string
            request_info["method"] = request.method
            request_info["headers"] = _filter_headers(dict(request.headers))

            if should_send_default_pii():
                if len(request.access_route) >= 1:
                    request_info["env"] = {"REMOTE_ADDR": request.access_route[0]}

                current_user_id = _get_current_user_id_from_quart()
                if current_user_id:
                    user_info = event.setdefault("user", {})
                    user_info["id"] = current_user_id

        return event

    return inner


async def _capture_exception(
    sender: "Quart", exception: "Union[ValueError, BaseException]", **kwargs: "Any"
) -> None:
    integration = sentry_sdk.get_client().get_integration(QuartIntegration)
    if integration is None:
        return

    event, hint = event_from_exception(
        exception,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "quart", "handled": False},
    )

    sentry_sdk.capture_event(event, hint=hint)


def _get_current_user_id_from_quart() -> "str | None":
    if quart_auth is None:
        return None

    if quart_auth.current_user is None:
        return None

    try:
        return quart_auth.current_user._auth_id
    except Exception:
        return None

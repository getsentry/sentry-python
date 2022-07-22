from sentry_sdk._types import MYPY
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.starlette import (
    SentryStarletteMiddleware,
    StarletteIntegration,
)
from sentry_sdk.tracing import SOURCE_FOR_STYLE, TRANSACTION_SOURCE_ROUTE
from sentry_sdk.utils import transaction_from_function

if MYPY:
    from typing import Any, Callable, Dict

    from sentry_sdk._types import Event

try:
    from fastapi import FastAPI  # type: ignore
    from fastapi import Request
except ImportError:
    raise DidNotEnable("FastAPI is not installed")

try:
    from starlette.types import ASGIApp, Receive, Scope, Send  # type: ignore
except ImportError:
    raise DidNotEnable("Starlette is not installed")


_DEFAULT_TRANSACTION_NAME = "generic FastAPI request"


class FastApiIntegration(StarletteIntegration):
    identifier = "fastapi"

    @staticmethod
    def setup_once():
        # type: () -> None
        StarletteIntegration.setup_once()
        patch_middlewares()


def patch_middlewares():
    # type: () -> None

    old_build_middleware_stack = FastAPI.build_middleware_stack

    def _sentry_build_middleware_stack(self):
        # type: (FastAPI) -> Callable[..., Any]
        """
        Adds `SentryStarletteMiddleware` and `SentryFastApiMiddleware` to the
        middleware stack of the FastAPI application.
        """
        app = old_build_middleware_stack(self)
        app = SentryStarletteMiddleware(app=app)
        app = SentryFastApiMiddleware(app=app)
        return app

    FastAPI.build_middleware_stack = _sentry_build_middleware_stack


def _set_transaction_name_and_source(event, transaction_style, request):
    # type: (Event, str, Any) -> None
    name = ""

    if transaction_style == "endpoint":
        endpoint = request.scope.get("endpoint")
        if endpoint:
            name = transaction_from_function(endpoint) or ""

    elif transaction_style == "url":
        route = request.scope.get("route")
        if route:
            path = getattr(route, "path", None)
            if path is not None:
                name = path

    if not name:
        event["transaction"] = _DEFAULT_TRANSACTION_NAME
        event["transaction_info"] = {"source": TRANSACTION_SOURCE_ROUTE}
        return

    event["transaction"] = name
    event["transaction_info"] = {"source": SOURCE_FOR_STYLE[transaction_style]}


class SentryFastApiMiddleware:
    def __init__(self, app, dispatch=None):
        # type: (ASGIApp, Any) -> None
        self.app = app

    async def __call__(self, scope, receive, send):
        # type: (Scope, Receive, Send) -> Any
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hub = Hub.current
        integration = hub.get_integration(FastApiIntegration)
        if integration is None:
            return

        with hub.configure_scope() as sentry_scope:
            request = Request(scope, receive=receive, send=send)

            def _make_request_event_processor(req, integration):
                # type: (Any, Any) -> Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
                def event_processor(event, hint):
                    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]

                    _set_transaction_name_and_source(
                        event, integration.transaction_style, req
                    )

                    return event

                return event_processor

            sentry_scope._name = FastApiIntegration.identifier
            sentry_scope.add_event_processor(
                _make_request_event_processor(request, integration)
            )

            await self.app(scope, receive, send)

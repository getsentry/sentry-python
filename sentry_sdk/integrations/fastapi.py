import re

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
    from typing import Any, Awaitable, Callable, Dict

    from sentry_sdk._types import Event

try:
    from fastapi.applications import FastAPI
    from fastapi.requests import Request
except ImportError:
    raise DidNotEnable("FastAPI is not installed")


_DEFAULT_TRANSACTION_NAME = "generic FastApi request"
_DEFAULT_TRANSACTION_NAME_REGEX = r"generic \S+ request"


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
        # type: (Callable[..., Any]) -> Callable[..., Any]
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
    print("fastapi: _set_transaction_name_and_source")

    is_default_transaction_name = "transaction" in event and re.match(
        _DEFAULT_TRANSACTION_NAME_REGEX, event["transaction"]
    )

    if not is_default_transaction_name:
        # Another integration already set the name, do not override
        return

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
        print("fastapi: set DEFAULT name and source")
        event["transaction"] = _DEFAULT_TRANSACTION_NAME
        event["transaction_info"] = {"source": TRANSACTION_SOURCE_ROUTE}

    print("fastapi: set FOUND name and source")
    event["transaction"] = name
    event["transaction_info"] = {"source": SOURCE_FOR_STYLE[transaction_style]}


class SentryFastApiMiddleware:
    def __init__(self, app, dispatch=None):
        # type: (SentryFastApiMiddleware, Any) -> None
        self.app = app

    async def __call__(self, scope, receive, send):
        # type: (SentryFastApiMiddleware, Dict[str, Any], Callable[[], Awaitable[Dict[str, Any]]], Callable[[Dict[str, Any]], Awaitable[None]]) -> Any
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

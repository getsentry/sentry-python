from __future__ import absolute_import

from sentry_sdk._types import MYPY
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.utils import event_from_exception


if MYPY:
    from typing import Any, Dict


try:
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.requests import Request
except ImportError:
    raise DidNotEnable("Starlette is not installed")

TRANSACTION_STYLE_VALUES = ("endpoint", "url")


class StarletteIntegration(Integration):
    identifier = "starlette"

    transaction_style = None

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

        old_app = Starlette.__call__

        async def sentry_patched_asgi_app(self, scope, receive, send):
            # type: (Any, Any, Any, Any) -> Any
            # TODO(neel): cleanup types
            if Hub.current.get_integration(StarletteIntegration) is None:
                return await old_app(self, scope, receive, send)

            # TODO(anton): make only call to SentryStarletteMiddleware and ditch the _sentry_build_middleware_stack patching.
            middleware = SentryAsgiMiddleware(
                lambda *a, **kw: old_app(self, *a, **kw),
                mechanism_type=StarletteIntegration.identifier,
            )
            middleware.__call__ = middleware._run_asgi3
            return await middleware(scope, receive, send)

        Starlette.__call__ = sentry_patched_asgi_app
        _patch_exception_middleware()

        original_build_middleware_stack = Starlette.build_middleware_stack

        def _sentry_build_middleware_stack(self):
            app = original_build_middleware_stack(self)

            middleware = [
                Middleware(
                    SentryStarletteMiddleware,
                )
            ]
            for cls, options in reversed(middleware):
                app = cls(app=app, **options)

            return app

        Starlette.build_middleware_stack = _sentry_build_middleware_stack


def _capture_exception(exception, handled=False):
    # type: (BaseException, **Any) -> None
    hub = Hub.current
    if hub.get_integration(StarletteIntegration) is None:
        return

    event, hint = event_from_exception(
        exception,
        client_options=hub.client.options,
        mechanism={"type": StarletteIntegration.identifier, "handled": handled},
    )

    hub.capture_event(event, hint=hint)


def _patch_exception_middleware():
    from starlette.exceptions import ExceptionMiddleware

    old_http_exception = ExceptionMiddleware.http_exception

    def sentry_patched_http_exception(self, request, exc):
        _capture_exception(exc, handled=True)
        return old_http_exception(self, request, exc)

    ExceptionMiddleware.http_exception = sentry_patched_http_exception


def _make_request_event_processor(req, integration):
    # TODO: add types

    async def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        from starlette.routing import Match

        router = req.scope["router"]

        for route in router.routes:
            match = route.matches(req.scope)
            if match[0] == Match.FULL:
                if integration.transaction_style == "endpoint":  # function name
                    event["transaction"] = match[1]["endpoint"].__name__
                elif integration.transaction_style == "url":  # url low cardinality
                    event["transaction"] = route.path

        # TODO(anton) put request extractor here.
        return event

    return inner


class SentryStarletteMiddleware(SentryAsgiMiddleware):
    def __init__(self, app, dispatch=None):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hub = Hub.current
        integration = hub.get_integration(StarletteIntegration)
        if integration is None:
            return

        with hub.configure_scope() as sentry_scope:
            request = Request(scope, receive=receive, send=send)

            sentry_scope._name = StarletteIntegration.identifier
            sentry_scope.add_event_processor(
                _make_request_event_processor(request, integration)
            )

            await self.app(scope, receive, send)

from __future__ import absolute_import

from sentry_sdk.hub import Hub
from sentry_sdk.utils import event_from_exception
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any


try:
    from starlette.applications import Starlette
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

            middleware = SentryAsgiMiddleware(
                lambda *a, **kw: old_app(self, *a, **kw),
                mechanism_type=StarletteIntegration.identifier,
            )
            middleware.__call__ = middleware._run_asgi3
            return await middleware(scope, receive, send)

        Starlette.__call__ = sentry_patched_asgi_app
        _patch_exception_middleware()


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

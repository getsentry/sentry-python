from sentry_sdk._types import MYPY
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.starlette import (
    SentryStarletteMiddleware,
    StarletteIntegration,
)

if MYPY:
    from typing import Any, Callable

try:
    from fastapi.applications import FastAPI
except ImportError:
    raise DidNotEnable("FastAPI is not installed")


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
        Adds `SentryStarletteMiddleware` to the
        middleware stack of the Starlette application.
        """
        app = old_build_middleware_stack(self)
        app = SentryStarletteMiddleware(app=app)
        return app

    FastAPI.build_middleware_stack = _sentry_build_middleware_stack

from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.tracing import SOURCE_FOR_STYLE, TransactionSource
from sentry_sdk.utils import transaction_from_function

if TYPE_CHECKING:
    from typing import Any, Callable, Optional

try:
    from sentry_sdk.integrations.starlette import (
        StarletteIntegration,
        _is_async_callable,
        _wrap_sync_handler,
    )
except DidNotEnable:
    raise DidNotEnable("Starlette is not installed")

try:
    import fastapi  # type: ignore
    from starlette.requests import HTTPConnection, Request
except ImportError:
    raise DidNotEnable("FastAPI is not installed")


_DEFAULT_TRANSACTION_NAME = "generic FastAPI request"


class FastApiIntegration(StarletteIntegration):
    identifier = "fastapi"

    @staticmethod
    def setup_once() -> None:
        patch_fastapi_init()


async def _sentry_fastapi_dependency(request: "HTTPConnection") -> None:
    if not isinstance(request, Request):
        return

    integration = sentry_sdk.get_client().get_integration(FastApiIntegration)
    if integration is None:
        return

    current_scope = sentry_sdk.get_current_scope()
    route = request.scope.get("route")

    route_path = None
    if route:
        effective_route_context = request.scope.get("fastapi", {}).get(
            "effective_route_context"
        )
        context_path = getattr(effective_route_context, "path", None)

        if context_path:
            route_path = context_path
        else:
            path = getattr(route, "path", None)
            if path is not None:
                route_path = path

    server_span = current_scope._server_segment_span
    if server_span is not None and route_path is not None:
        server_span.set_attribute(SPANDATA.HTTP_ROUTE, route_path)

    _set_transaction_name_and_source(
        current_scope,
        integration.transaction_style,
        endpoint=request.scope.get("endpoint"),
        route_path=route_path,
    )

    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if (
        dependant is not None
        and dependant.call is not None
        and not _is_async_callable(dependant.call)
    ):
        dependant.call = _wrap_sync_handler(dependant.call)

    sentry_sdk.get_isolation_scope()._name = FastApiIntegration.identifier


def patch_fastapi_init() -> None:
    old_fastapi_init = fastapi.FastAPI.__init__

    if getattr(old_fastapi_init, "_sentry_is_patched", False):
        return

    @wraps(old_fastapi_init)
    def _sentry_fastapi_init(self: "Any", *args: "Any", **kwargs: "Any") -> None:
        dependencies = kwargs.get("dependencies")
        if dependencies is None:
            dependencies = []

        kwargs["dependencies"] = [
            *dependencies,
            fastapi.Depends(_sentry_fastapi_dependency),
        ]

        old_fastapi_init(self, *args, **kwargs)

    _sentry_fastapi_init._sentry_is_patched = True  # type: ignore[attr-defined]
    fastapi.FastAPI.__init__ = _sentry_fastapi_init


def _set_transaction_name_and_source(
    scope: "sentry_sdk.Scope",
    transaction_style: str,
    endpoint: "Optional[Callable[..., Any]]",
    route_path: "Optional[str]",
) -> None:
    name = ""

    if transaction_style == "endpoint" and endpoint:
        name = transaction_from_function(endpoint) or ""

    elif transaction_style == "url" and route_path is not None:
        name = route_path

    if not name:
        name = _DEFAULT_TRANSACTION_NAME
        source = TransactionSource.ROUTE
    else:
        source = SOURCE_FOR_STYLE[transaction_style]

    scope.set_transaction_name(name, source=source)


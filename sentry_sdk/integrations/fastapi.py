from copy import deepcopy
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.traces import StreamedSpan, get_current_span
from sentry_sdk.tracing import SOURCE_FOR_STYLE, TransactionSource
from sentry_sdk.utils import has_data_collection_enabled, transaction_from_function

if TYPE_CHECKING:
    from typing import Any, Callable, Optional

try:
    from sentry_sdk.integrations.starlette import (
        StarletteIntegration,
        StarletteRequestExtractor,
        _get_cached_request_body_attribute,
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


async def _sentry_fastapi_dependency(request: "HTTPConnection"):
    if not isinstance(request, Request):
        yield
        return

    client = sentry_sdk.get_client()
    integration = client.get_integration(FastApiIntegration)
    if integration is None:
        yield
        return

    current_scope = sentry_sdk.get_current_scope()
    effective_route_context = request.scope.get("fastapi", {}).get(
        "effective_route_context"
    )
    route = request.scope.get("route")

    route_path = None
    if effective_route_context is not None:
        route_path = getattr(effective_route_context, "path", None)

    if route_path is None and route is not None:
        route_path = getattr(route, "path", None)

    server_span = current_scope._server_segment_span
    if server_span is not None and route_path is not None:
        server_span.set_attribute(SPANDATA.HTTP_ROUTE, route_path)

    _set_transaction_name_and_source(
        current_scope,
        integration.transaction_style,
        endpoint=request.scope.get("endpoint"),
        route_path=route_path,
    )

    # FastAPI may execute the dependant stored on the effective route context
    # instead of the original APIRoute.
    dependant = getattr(effective_route_context, "dependant", None)
    if dependant is None:
        dependant = getattr(route, "dependant", None)
    if (
        dependant is not None
        and dependant.call is not None
        and not _is_async_callable(dependant.call)
    ):
        dependant.call = _wrap_sync_handler(dependant.call)

    sentry_scope = sentry_sdk.get_isolation_scope()
    extractor = StarletteRequestExtractor(request)
    info = await extractor.extract_request_info()

    def _make_request_event_processor(
        info: "dict[str, Any]",
    ) -> "Callable[[Any, dict[str, Any]], Any]":
        def event_processor(
            event: "dict[str, Any]", hint: "dict[str, Any]"
        ) -> "dict[str, Any]":
            event_request = event.get("request", {})
            if info:
                if "cookies" in info:
                    event_request["cookies"] = info["cookies"]
                if "data" in info:
                    attach_request_data = True
                    if has_data_collection_enabled(client.options):
                        attach_request_data = (
                            "incoming_request"
                            in client.options["data_collection"]["http_bodies"]
                        )

                    if attach_request_data:
                        event_request["data"] = info["data"]
            event["request"] = deepcopy(event_request)
            return event

        return event_processor

    sentry_scope._name = FastApiIntegration.identifier
    sentry_scope.add_event_processor(_make_request_event_processor(info))

    try:
        yield
    finally:
        current_span = get_current_span()
        if type(current_span) is StreamedSpan:
            attach_request_data = True
            if has_data_collection_enabled(client.options):
                attach_request_data = (
                    "incoming_request"
                    in client.options["data_collection"]["http_bodies"]
                )

            if attach_request_data:
                request_body = _get_cached_request_body_attribute(
                    client=client, request=request
                )
                if request_body:
                    current_span._segment.set_attribute(
                        SPANDATA.HTTP_REQUEST_BODY_DATA,
                        request_body,
                    )


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
            fastapi.Depends(_sentry_fastapi_dependency),
            *dependencies,
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
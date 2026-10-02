import sys
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
    from typing import Any, Awaitable, Callable, Dict, Optional

    from sentry_sdk._types import Event

try:
    from sentry_sdk.integrations.starlette import (
        StarletteIntegration,
        StarletteRequestExtractor,
        _get_cached_request_body_attribute,
        _update_active_thread,
    )
except DidNotEnable:
    raise DidNotEnable("Starlette is not installed")

try:
    import fastapi  # type: ignore
    from starlette.requests import HTTPConnection, Request
except ImportError:
    raise DidNotEnable("FastAPI is not installed")


_DEFAULT_TRANSACTION_NAME = "generic FastAPI request"


# Vendored: https://github.com/Kludex/starlette/blob/0a29b5ccdcbd1285c75c4fdb5d62ae1d244a21b0/starlette/_utils.py#L11-L17
if sys.version_info >= (3, 13):  # pragma: no cover
    from inspect import iscoroutinefunction
else:
    from asyncio import iscoroutinefunction


class FastApiIntegration(StarletteIntegration):
    identifier = "fastapi"

    @staticmethod
    def setup_once() -> None:
        patch_fastapi_init()


def _sentry_fastapi_dependency(request: "HTTPConnection") -> None:
    if not isinstance(request, Request):
        return

    endpoint = request.scope.get("endpoint")
    if endpoint is not None and iscoroutinefunction(endpoint):
        return

    _update_active_thread()


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


async def _wrap_async_handler(
    handler: "Callable[..., Awaitable[Any]]", *args: "Any", **kwargs: "Any"
) -> "Any":
    """
    Wraps an asynchronous handler function to attach request info to errors and the server segment span.
    The request body cached on the Starlette Request object is attached to streamed spans, but consuming the request body in the event
    processor can still cause application hangs.
    """
    client = sentry_sdk.get_client()
    integration = client.get_integration(FastApiIntegration)
    if integration is None:
        return await handler(*args, **kwargs)

    request = args[0]

    route = request.scope.get("route")

    route_path = None
    if route:
        # FastAPI >= 0.137 stores the prefix-resolved path on an
        # effective_route_context in scope["fastapi"], while
        # scope["route"].path holds the unprefixed original.
        # Prefer the effective context path when available.
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

    server_span = sentry_sdk.get_current_scope()._server_segment_span
    if server_span is not None and route_path is not None:
        server_span.set_attribute(SPANDATA.HTTP_ROUTE, route_path)

    _set_transaction_name_and_source(
        sentry_sdk.get_current_scope(),
        integration.transaction_style,
        endpoint=request.scope.get("endpoint"),
        route_path=route_path,
    )
    sentry_scope = sentry_sdk.get_isolation_scope()
    extractor = StarletteRequestExtractor(request)
    info = await extractor.extract_request_info()

    def _make_request_event_processor(
        req: "Any", integration: "Any"
    ) -> "Callable[[Event, Dict[str, Any]], Event]":
        def event_processor(event: "Event", hint: "Dict[str, Any]") -> "Event":
            # Extract information from request
            request_info = event.get("request", {})
            if info:
                if "cookies" in info:
                    request_info["cookies"] = info["cookies"]
                if "data" in info:
                    attach_request_data = True
                    if has_data_collection_enabled(client.options):
                        attach_request_data = (
                            "incoming_request"
                            in client.options["data_collection"]["http_bodies"]
                        )

                    if attach_request_data:
                        request_info["data"] = info["data"]
            event["request"] = deepcopy(request_info)

            return event

        return event_processor

    sentry_scope._name = FastApiIntegration.identifier
    sentry_scope.add_event_processor(
        _make_request_event_processor(request, integration)
    )

    try:
        return await handler(*args, **kwargs)
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

import sys
from copy import deepcopy
from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import NoOpStreamedSpan, StreamedSpan
from sentry_sdk.tracing import SOURCE_FOR_STYLE, TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import transaction_from_function

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict
    from sentry_sdk._types import Event

try:
    from sentry_sdk.integrations.starlette import (
        StarletteIntegration,
        StarletteRequestExtractor,
        _set_request_body_data_on_streaming_segment,
    )
except DidNotEnable:
    raise DidNotEnable("Starlette is not installed")

try:
    import fastapi  # type: ignore
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
        patch_get_request_handler()


def _set_transaction_name_and_source(
    scope: "sentry_sdk.Scope", transaction_style: str, request: "Any"
) -> None:
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
        name = _DEFAULT_TRANSACTION_NAME
        source = TransactionSource.ROUTE
    else:
        source = SOURCE_FOR_STYLE[transaction_style]

    scope.set_transaction_name(name, source=source)


def patch_get_request_handler() -> None:
    old_get_request_handler = fastapi.routing.get_request_handler

    def _sentry_get_request_handler(*args: "Any", **kwargs: "Any") -> "Any":
        dependant = kwargs.get("dependant")
        if (
            dependant
            and dependant.call is not None
            and not iscoroutinefunction(dependant.call)
        ):
            old_call = dependant.call

            @wraps(old_call)
            def _sentry_call(*args: "Any", **kwargs: "Any") -> "Any":
                current_scope = sentry_sdk.get_current_scope()
                current_span = current_scope.span

                if isinstance(current_span, StreamedSpan) and not isinstance(
                    current_span, NoOpStreamedSpan
                ):
                    segment = current_span._segment
                    segment._update_active_thread()
                elif current_scope.transaction is not None:
                    current_scope.transaction.update_active_thread()

                sentry_scope = sentry_sdk.get_isolation_scope()
                if sentry_scope.profile is not None:
                    sentry_scope.profile.update_active_thread_id()

                return old_call(*args, **kwargs)

            dependant.call = _sentry_call

        old_app = old_get_request_handler(*args, **kwargs)

        async def _sentry_app(*args: "Any", **kwargs: "Any") -> "Any":
            client = sentry_sdk.get_client()
            integration = client.get_integration(FastApiIntegration)
            if integration is None:
                return await old_app(*args, **kwargs)

            request = args[0]

            _set_transaction_name_and_source(
                sentry_sdk.get_current_scope(), integration.transaction_style, request
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
                        if "cookies" in info and should_send_default_pii():
                            request_info["cookies"] = info["cookies"]
                        if "data" in info:
                            request_info["data"] = info["data"]
                    event["request"] = deepcopy(request_info)

                    return event

                return event_processor

            sentry_scope._name = FastApiIntegration.identifier
            sentry_scope.add_event_processor(
                _make_request_event_processor(request, integration)
            )

            if has_span_streaming_enabled(client.options):
                _set_request_body_data_on_streaming_segment(info)

            return await old_app(*args, **kwargs)

        return _sentry_app

    fastapi.routing.get_request_handler = _sentry_get_request_handler

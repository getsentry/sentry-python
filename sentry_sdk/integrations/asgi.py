"""
An ASGI middleware.

Based on Tom Christie's `sentry-asgi <https://github.com/encode/sentry-asgi>`.
"""

import asyncio
import inspect
import logging
from copy import deepcopy

from sentry_sdk._functools import partial
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.api import continue_trace
from sentry_sdk.consts import OP
from sentry_sdk.hub import Hub
from sentry_sdk.integrations._asgi_common import (
    _get_headers,
    _get_request_data,
    _get_url,
)
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.sessions import auto_session_tracking
from sentry_sdk.tracing import (
    SOURCE_FOR_STYLE,
    TRANSACTION_SOURCE_ROUTE,
    TRANSACTION_SOURCE_URL,
)
from sentry_sdk.utils import (
    ContextVar,
    event_from_exception,
    HAS_REAL_CONTEXTVARS,
    CONTEXTVARS_ERROR_MESSAGE,
    logger,
    transaction_from_function,
)
from sentry_sdk.tracing import Transaction

if TYPE_CHECKING:
    from typing import Dict
    from typing import Any
    from typing import Optional
    from typing import Callable

    from sentry_sdk._types import Event, Hint


_asgi_middleware_applied = ContextVar("sentry_asgi_middleware_applied")

_DEFAULT_TRANSACTION_NAME = "generic ASGI request"

TRANSACTION_STYLE_VALUES = ("endpoint", "url")


def _capture_exception(hub, exc, mechanism_type="asgi"):
    # type: (Hub, Any, str) -> None

    # Check client here as it might have been unset while streaming response
    if hub.client is not None:
        event, hint = event_from_exception(
            exc,
            client_options=hub.client.options,
            mechanism={"type": mechanism_type, "handled": False},
        )
        hub.capture_event(event, hint=hint)


def _looks_like_asgi3(app):
    # type: (Any) -> bool
    """
    Try to figure out if an application object supports ASGI3.

    This is how uvicorn figures out the application version as well.
    """
    if inspect.isclass(app):
        return hasattr(app, "__await__")
    elif inspect.isfunction(app):
        return asyncio.iscoroutinefunction(app)
    else:
        call = getattr(app, "__call__", None)  # noqa
        return asyncio.iscoroutinefunction(call)


class SentryAsgiMiddleware:
    __slots__ = ("app", "__call__", "transaction_style", "mechanism_type")

    def __init__(
        self,
        app,
        unsafe_context_data=False,
        transaction_style="endpoint",
        mechanism_type="asgi",
    ):
        # type: (Any, bool, str, str) -> None
        """
        Instrument an ASGI application with Sentry. Provides HTTP/websocket
        data to sent events and basic handling for exceptions bubbling up
        through the middleware.

        :param unsafe_context_data: Disable errors when a proper contextvars installation could not be found. We do not recommend changing this from the default.
        """
        if not unsafe_context_data and not HAS_REAL_CONTEXTVARS:
            # We better have contextvars or we're going to leak state between
            # requests.
            raise RuntimeError(
                "The ASGI middleware for Sentry requires Python 3.7+ "
                "or the aiocontextvars package." + CONTEXTVARS_ERROR_MESSAGE
            )
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )

        asgi_middleware_while_using_starlette_or_fastapi = (
            mechanism_type == "asgi" and "starlette" in _get_installed_modules()
        )
        if asgi_middleware_while_using_starlette_or_fastapi:
            logger.warning(
                "The Sentry Python SDK can now automatically support ASGI frameworks like Starlette and FastAPI. "
                "Please remove 'SentryAsgiMiddleware' from your project. "
                "See https://docs.sentry.io/platforms/python/guides/asgi/ for more information."
            )

        self.transaction_style = transaction_style
        self.mechanism_type = mechanism_type
        self.app = app

        if _looks_like_asgi3(app):
            self.__call__ = self._run_asgi3  # type: Callable[..., Any]
        else:
            self.__call__ = self._run_asgi2

    def _run_asgi2(self, scope):
        # type: (Any) -> Any
        async def inner(receive, send):
            # type: (Any, Any) -> Any
            return await self._run_app(scope, receive, send, asgi_version=2)

        return inner

    async def _run_asgi3(self, scope, receive, send):
        # type: (Any, Any, Any) -> Any
        return await self._run_app(scope, receive, send, asgi_version=3)

    async def _run_app(self, scope, receive, send, asgi_version):
        # type: (Any, Any, Any, Any, int) -> Any
        is_recursive_asgi_middleware = _asgi_middleware_applied.get(False)
        if is_recursive_asgi_middleware:
            try:
                if asgi_version == 2:
                    return await self.app(scope)(receive, send)
                else:
                    return await self.app(scope, receive, send)

            except Exception as exc:
                _capture_exception(Hub.current, exc, mechanism_type=self.mechanism_type)
                raise exc from None

        _asgi_middleware_applied.set(True)
        try:
            hub = Hub(Hub.current)
            with auto_session_tracking(hub, session_mode="request"):
                with hub:
                    with hub.configure_scope() as sentry_scope:
                        sentry_scope.clear_breadcrumbs()
                        sentry_scope._name = "asgi"
                        processor = partial(self.event_processor, asgi_scope=scope)
                        sentry_scope.add_event_processor(processor)

                    ty = scope["type"]

                    if ty in ("http", "websocket"):
                        (
                            transaction_name,
                            transaction_source,
                        ) = self._get_transaction_name_and_source(
                            self.transaction_style, scope
                        )
                        transaction = continue_trace(
                            _get_headers(scope),
                            op="{}.server".format(ty),
                            name=transaction_name,
                            source=transaction_source,
                        )
                        logging.warning(
                            "[ASGI] Created Transaction %s, %s, %s",
                            transaction,
                            transaction.sampled,
                            transaction_name,
                        )
                    else:
                        transaction = Transaction(op=OP.HTTP_SERVER)

                    transaction.set_tag("asgi.type", ty)

                    logging.warning(
                        "[ASGI] Starting Transaction %s, %s, %s",
                        transaction,
                        transaction.sampled,
                        transaction.name,
                    )
                    with hub.start_transaction(
                        transaction, custom_sampling_context={"asgi_scope": scope}
                    ):
                        try:

                            async def _sentry_wrapped_send(event):
                                # type: (Dict[str, Any]) -> Any
                                is_http_response = (
                                    event.get("type") == "http.response.start"
                                    and transaction is not None
                                    and "status" in event
                                )
                                if is_http_response:
                                    transaction.set_http_status(event["status"])

                                return await send(event)

                            if asgi_version == 2:
                                return await self.app(scope)(
                                    receive, _sentry_wrapped_send
                                )
                            else:
                                return await self.app(
                                    scope, receive, _sentry_wrapped_send
                                )
                        except Exception as exc:
                            _capture_exception(
                                hub, exc, mechanism_type=self.mechanism_type
                            )
                            raise exc from None
        finally:
            _asgi_middleware_applied.set(False)

    def event_processor(self, event, hint, asgi_scope):
        # type: (Event, Hint, Any) -> Optional[Event]
        request_data = event.get("request", {})
        request_data.update(_get_request_data(asgi_scope))
        event["request"] = deepcopy(request_data)

        transaction_name, transaction_source = self._get_transaction_name_and_source(
            self.transaction_style, asgi_scope
        )
        event["transaction"] = transaction_name
        event["transaction_info"] = {"source": transaction_source}

        return event

    def _get_transaction_name_and_source(self, transaction_style, asgi_scope):
        # type: (Event, str, Any) -> None
        name = None
        source = None

        if transaction_style == "endpoint":
            endpoint = asgi_scope.get("endpoint")
            # Webframeworks like Starlette mutate the ASGI env once routing is
            # done, which is sometime after the request has started. If we have
            # an endpoint, overwrite our generic transaction name.
            if endpoint:
                name = transaction_from_function(endpoint) or ""
                source = SOURCE_FOR_STYLE[transaction_style]
            else:
                ty = asgi_scope.get("type")
                if ty in ("http", "websocket"):
                    name = _get_url(
                        asgi_scope, "http" if ty == "http" else "ws", host=None
                    )
                    source = TRANSACTION_SOURCE_URL
                else:
                    name = _DEFAULT_TRANSACTION_NAME
                    source = TRANSACTION_SOURCE_ROUTE

        elif transaction_style == "url":
            route = asgi_scope.get("route")
            if route:
                name = route
                source = SOURCE_FOR_STYLE[transaction_style]
            else:
                ty = asgi_scope.get("type")
                if ty in ("http", "websocket"):
                    name = _get_url(
                        asgi_scope, "http" if ty == "http" else "ws", host=None
                    )
                    source = TRANSACTION_SOURCE_URL
                else:
                    name = _DEFAULT_TRANSACTION_NAME
                    source = TRANSACTION_SOURCE_ROUTE

        if name is None:
            name = _DEFAULT_TRANSACTION_NAME
            source = TRANSACTION_SOURCE_ROUTE

        return name, source

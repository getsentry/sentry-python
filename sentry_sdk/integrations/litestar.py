from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scope import Scope as SentryScope, should_send_default_pii
from sentry_sdk.tracing import SOURCE_FOR_STYLE, TRANSACTION_SOURCE_ROUTE
from sentry_sdk.utils import (
    ensure_integration_enabled,
    event_from_exception,
    transaction_from_function,
)

try:
    from litestar import Request, Litestar  # type: ignore
    from litestar.handlers.base import BaseRouteHandler  # type: ignore
    from litestar.middleware import DefineMiddleware  # type: ignore
    from litestar.routes.http import HTTPRoute  # type: ignore
    from litestar.data_extractors import ConnectionDataExtractor  # type: ignore

    if TYPE_CHECKING:
        from typing import Any, Dict, List, Optional, Union

        from litestar.types.asgi_types import ASGIApp  # type: ignore
        from litestar.types import (  # type: ignore
            HTTPReceiveMessage,
            HTTPScope,
            Message,
            Middleware,
            Receive,
            Scope as LitestarScope,
            Send,
            WebSocketReceiveMessage,
        )
        from litestar.middleware import MiddlewareProtocol
        from sentry_sdk._types import Event, Hint
except ImportError:
    raise DidNotEnable("Litestar is not installed")


_DEFAULT_TRANSACTION_NAME = "generic Litestar request"


class LitestarIntegration(Integration):
    identifier = "litestar"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        patch_app_init()
        patch_middlewares()
        patch_http_route_handle()

        # logs an error for every 500
        ignore_logger("litestar")


class SentryLitestarASGIMiddleware(SentryAsgiMiddleware):
    def __init__(self, app: "ASGIApp", span_origin: str = LitestarIntegration.origin):
        super().__init__(
            app=app,
            unsafe_context_data=False,
            transaction_style="endpoint",
            mechanism_type="asgi",
            span_origin=span_origin,
        )


def patch_app_init() -> None:
    """
    Replaces the Litestar class's `__init__` function in order to inject `after_exception` handlers and set the
    `SentryLitestarASGIMiddleware` as the outmost middleware in the stack.
    See:
    - https://docs.litestar.dev/2/usage/applications.html#after-exception
    - https://docs.litestar.dev/2/usage/middleware/using-middleware.html
    """
    old__init__ = Litestar.__init__

    def injection_wrapper(self: "Litestar", *args: "Any", **kwargs: "Any") -> None:
        after_exception = kwargs.pop("after_exception", [])
        kwargs.update(
            after_exception=[
                exception_handler,
                *(
                    after_exception
                    if isinstance(after_exception, list)
                    else [after_exception]
                ),
            ]
        )

        SentryLitestarASGIMiddleware.__call__ = SentryLitestarASGIMiddleware._run_asgi3  # type: ignore
        middleware = kwargs.pop("middleware", None) or []
        kwargs["middleware"] = [SentryLitestarASGIMiddleware, *middleware]
        old__init__(self, *args, **kwargs)

    Litestar.__init__ = injection_wrapper


def patch_middlewares() -> None:
    old__resolve_middleware_stack = BaseRouteHandler.resolve_middleware

    def resolve_middleware_wrapper(self: "Any") -> "List[Middleware]":
        return [
            enable_span_for_middleware(middleware)
            for middleware in old__resolve_middleware_stack(self)
        ]

    BaseRouteHandler.resolve_middleware = resolve_middleware_wrapper


def enable_span_for_middleware(middleware: "Middleware") -> "Middleware":
    if (
        not hasattr(middleware, "__call__")  # noqa: B004
        or middleware is SentryLitestarASGIMiddleware
    ):
        return middleware

    if isinstance(middleware, DefineMiddleware):
        old_call: "ASGIApp" = middleware.middleware.__call__
    else:
        old_call = middleware.__call__

    async def _create_span_call(
        self: "MiddlewareProtocol",
        scope: "LitestarScope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        if sentry_sdk.get_client().get_integration(LitestarIntegration) is None:
            return await old_call(self, scope, receive, send)

        middleware_name = self.__class__.__name__
        with sentry_sdk.start_span(
            op=OP.MIDDLEWARE_LITESTAR,
            description=middleware_name,
            origin=LitestarIntegration.origin,
        ) as middleware_span:
            middleware_span.set_tag("litestar.middleware_name", middleware_name)

            # Creating spans for the "receive" callback
            async def _sentry_receive(
                *args: "Any", **kwargs: "Any"
            ) -> "Union[HTTPReceiveMessage, WebSocketReceiveMessage]":
                with sentry_sdk.start_span(
                    op=OP.MIDDLEWARE_LITESTAR_RECEIVE,
                    description=getattr(receive, "__qualname__", str(receive)),
                    origin=LitestarIntegration.origin,
                ) as span:
                    span.set_tag("litestar.middleware_name", middleware_name)
                    return await receive(*args, **kwargs)

            receive_name = getattr(receive, "__name__", str(receive))
            receive_patched = receive_name == "_sentry_receive"
            new_receive = _sentry_receive if not receive_patched else receive

            # Creating spans for the "send" callback
            async def _sentry_send(message: "Message") -> None:
                with sentry_sdk.start_span(
                    op=OP.MIDDLEWARE_LITESTAR_SEND,
                    description=getattr(send, "__qualname__", str(send)),
                    origin=LitestarIntegration.origin,
                ) as span:
                    span.set_tag("litestar.middleware_name", middleware_name)
                    return await send(message)

            send_name = getattr(send, "__name__", str(send))
            send_patched = send_name == "_sentry_send"
            new_send = _sentry_send if not send_patched else send

            return await old_call(self, scope, new_receive, new_send)

    not_yet_patched = old_call.__name__ not in ["_create_span_call"]

    if not_yet_patched:
        if isinstance(middleware, DefineMiddleware):
            middleware.middleware.__call__ = _create_span_call
        else:
            middleware.__call__ = _create_span_call

    return middleware


def patch_http_route_handle() -> None:
    old_handle = HTTPRoute.handle

    async def handle_wrapper(
        self: "HTTPRoute", scope: "HTTPScope", receive: "Receive", send: "Send"
    ) -> None:
        if sentry_sdk.get_client().get_integration(LitestarIntegration) is None:
            return await old_handle(self, scope, receive, send)

        sentry_scope = SentryScope.get_isolation_scope()
        request: "Request[Any, Any]" = scope["app"].request_class(
            scope=scope, receive=receive, send=send
        )
        extracted_request_data = ConnectionDataExtractor(
            parse_body=True, parse_query=True
        )(request)
        body = extracted_request_data.pop("body")

        request_data = await body

        def event_processor(event: "Event", _: "Hint") -> "Event":
            route_handler = scope.get("route_handler")

            request_info = event.get("request", {})
            request_info["content_length"] = len(scope.get("_body", b""))
            if should_send_default_pii():
                request_info["cookies"] = extracted_request_data["cookies"]
            if request_data is not None:
                request_info["data"] = request_data

            func = None
            if route_handler.name is not None:
                tx_name = route_handler.name
            else:
                func = route_handler.fn
            if func is not None:
                tx_name = transaction_from_function(func)

            tx_info = {"source": SOURCE_FOR_STYLE["endpoint"]}

            if not tx_name:
                tx_name = _DEFAULT_TRANSACTION_NAME
                tx_info = {"source": TRANSACTION_SOURCE_ROUTE}

            event.update(
                {
                    "request": request_info,
                    "transaction": tx_name,
                    "transaction_info": tx_info,
                }
            )
            return event

        sentry_scope._name = LitestarIntegration.identifier
        sentry_scope.add_event_processor(event_processor)

        return await old_handle(self, scope, receive, send)

    HTTPRoute.handle = handle_wrapper


def retrieve_user_from_scope(scope: "LitestarScope") -> "Optional[Dict[str, Any]]":
    scope_user = scope.get("user", {})
    if not scope_user:
        return None
    if isinstance(scope_user, dict):
        return scope_user
    if hasattr(scope_user, "asdict"):  # dataclasses
        return scope_user.asdict()

    # NOTE: The following section was not ported from the original StarliteIntegration based on starlite 1.x
    # because as get_plugin_for_value from starlite 1.x no longer exists in litestar 2.y, and it is not clear
    # what would need to be done here
    #
    # plugin = get_plugin_for_value(scope_user)
    # if plugin and not is_async_callable(plugin.to_dict):
    #     return plugin.to_dict(scope_user)

    return None


@ensure_integration_enabled(LitestarIntegration)
def exception_handler(exc: Exception, scope: "LitestarScope") -> None:
    user_info: "Optional[Dict[str, Any]]" = None
    if should_send_default_pii():
        user_info = retrieve_user_from_scope(scope)
    if user_info and isinstance(user_info, dict):
        sentry_scope = SentryScope.get_isolation_scope()
        sentry_scope.set_user(user_info)

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": LitestarIntegration.identifier, "handled": False},
    )

    sentry_sdk.capture_event(event, hint=hint)

from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.tracing import SOURCE_FOR_STYLE
from sentry_sdk.utils import event_from_exception, transaction_from_function

try:
    from typing import Dict, Any, Optional, List

    from sentry_sdk._types import Event

    from pydantic import BaseModel
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlite import Starlite, State, HTTPException, Request
    from starlite.handlers.base import BaseRouteHandler
    from starlite.plugins.base import get_plugin_for_value
    from starlite.routes.http import HTTPRoute
    from starlite.types import Middleware
    from starlite.types import Scope, HTTPScope, Send, Receive, ASGIApp
    from starlite.utils import ConnectionDataExtractor, is_async_callable
except ImportError:
    raise DidNotEnable("Starlite is not installed")


class SentryStarliteASGIMiddleware(SentryAsgiMiddleware):
    def __init__(self, app: "ASGIApp"):
        super().__init__(
            app=app,
            unsafe_context_data=False,
            transaction_style="endpoint",
            mechanism_type="asgi",
        )
        self.__call__ = self._run_asgi3


class StarliteIntegration(Integration):
    identifier = "starlite"

    @staticmethod
    def setup_once():
        patch_app_init()
        patch_middlewares()
        patch_http_route_handle()


def patch_app_init() -> None:
    """
    Replaces the Starlite class's `__init__` function in order to inject `after_exception` handlers and set the
    `SentryStarliteASGIMiddleware` as the outmost middleware in the stack.

    See:
    - https://starlite-api.github.io/starlite/usage/0-the-starlite-app/5-application-hooks/#after-exception
    - https://starlite-api.github.io/starlite/usage/7-middleware/0-middleware-intro/
    """
    old__init__ = Starlite.__init__  # type: ignore

    def injection_wrapper(self, *args, **kwargs):

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

        middleware = kwargs.pop("middleware", [])
        kwargs["middleware"] = [SentryStarliteASGIMiddleware, *middleware]
        old__init__(self, *args, **kwargs)

    Starlite.__init__ = injection_wrapper


def patch_middlewares() -> None:
    old__resolve_middleware_stack = BaseRouteHandler.resolve_middleware

    def resolve_middleware_wrapper(self: Any) -> List["Middleware"]:
        return list(
            map(
                lambda middleware: enable_span_for_middleware(middleware),
                old__resolve_middleware_stack(self),
            )
        )

    BaseRouteHandler.resolve_middleware = resolve_middleware_wrapper


def enable_span_for_middleware(middleware: "Middleware") -> "Middleware":
    if not hasattr(middleware, "__call__"):
        return middleware

    old_call = middleware.__call__

    async def create_span_call(*args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(StarliteIntegration)
        if integration is not None:
            middleware_name = args[0].__class__.__name__
            with hub.start_span(
                op="starlite.middleware", description=middleware_name
            ) as middleware_span:
                middleware_span.set_tag("starlite.middleware_name", middleware_name)

                await old_call(*args, **kwargs)
        else:
            await old_call(*args, **kwargs)

    not_yet_patched = old_call.__name__ not in [
        "_create_span_call",
        "_sentry_authenticationmiddleware_call",
        "_sentry_exceptionmiddleware_call",
    ]

    if not_yet_patched:
        middleware.__call__ = create_span_call

    return middleware


def patch_http_route_handle() -> None:
    old_handle = HTTPRoute.handle

    async def handle_wrapper(
        self, scope: "HTTPScope", receive: "Receive", send: "Send"
    ) -> None:
        hub = Hub.current
        integration = hub.get_integration(StarliteIntegration)
        if integration is None:
            return await old_handle(self, scope, receive, send)
        with hub.configure_scope() as sentry_scope:
            extracted_request_data = ConnectionDataExtractor(
                parse_body=True,
                parse_query=True,
            )(Request[Any, Any](scope))
            body = extracted_request_data.pop("body")
            request_data = await body

            def event_processor(event: "Event", _: Dict[str, Any]) -> "Event":
                route_handler = scope.get("route_handler")

                request_info = event.get("request", {})
                request_info["content_length"] = len(scope.get("_body", b""))  # type: ignore
                if _should_send_default_pii():
                    request_info["cookies"] = extracted_request_data["cookies"]
                if request_data is not None:
                    request_info["data"] = request_data

                event.update(
                    request=request_info,
                    transaction=route_handler.name
                    or transaction_from_function(route_handler.fn),
                    transaction_info={"source": SOURCE_FOR_STYLE["endpoint"]},
                )
                return event

            sentry_scope._name = StarliteIntegration.identifier
            sentry_scope.add_event_processor(event_processor)

            await old_handle(self, scope, receive, send)

    HTTPRoute.handle = handle_wrapper


def retrieve_user_from_scope(scope: "Scope") -> Optional[Dict[str, Any]]:
    scope_user = scope.get("user", {})
    if not scope_user or not _should_send_default_pii():
        return None

    if isinstance(scope_user, dict):
        return scope_user
    if isinstance(scope_user, BaseModel):
        return scope_user.dict()
    if hasattr(scope_user, "asdict"):  # dataclasses
        return scope_user.asdict()

    plugin = get_plugin_for_value(scope_user)
    if plugin and not is_async_callable(plugin.to_dict):
        return plugin.to_dict(scope_user)

    return None


def exception_handler(exc: Exception, scope: Scope, _: State):
    hub = Hub.current
    if hub.get_integration(StarliteIntegration) is None:
        return

    user_info = retrieve_user_from_scope(scope)
    if user_info and isinstance(user_info, dict):
        with hub.configure_scope() as sentry_scope:
            sentry_scope.set_user(user_info)

    if isinstance(exc, (StarletteHTTPException, HTTPException)):
        event, hint = event_from_exception(
            exc,
            client_options=hub.client.options if hub.client else None,
            mechanism={"type": StarliteIntegration.identifier, "handled": True},
        )

        hub.capture_event(event, hint=hint)

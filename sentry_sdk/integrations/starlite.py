from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.tracing import SOURCE_FOR_STYLE
from sentry_sdk.utils import event_from_exception, transaction_from_function

try:
    from typing import Dict, Any

    from sentry_sdk._types import Event

    from pydantic import BaseModel
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlite import Starlite, State, HTTPException, Request
    from starlite.types import Scope, HTTPScope, Send, Receive, ASGIApp
    from starlite.routes.http import HTTPRoute
    from starlite.utils.extractors import ConnectionDataExtractor
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


def retrieve_user_from_scope(scope: Scope) -> Dict[str, Any]:
    user_dict = {}
    if "user" not in scope and not _should_send_default_pii():
        return user_dict

    scope_user = scope.get("user")
    if isinstance(scope_user, dict):
        return scope_user
    elif isinstance(scope_user, BaseModel):
        return scope_user.dict()
    elif hasattr(scope_user, "asdict"):
        return scope_user.asdict()
    return user_dict


def exception_handler(exc: Exception, scope: Scope, _: State):
    hub = Hub.current
    if hub.get_integration(StarliteIntegration) is None:
        return

    user_info = retrieve_user_from_scope(scope)
    if user_info and isinstance(user_info, dict):
        with hub.configure_scope() as sentry_scope:
            if sentry_scope:
                sentry_scope.set_user(user_info)

    if isinstance(exc, (StarletteHTTPException, HTTPException)):
        event, hint = event_from_exception(
            exc,
            client_options=hub.client.options if hub.client else None,
            mechanism={"type": StarliteIntegration.identifier, "handled": True},
        )

        hub.capture_event(event, hint=hint)

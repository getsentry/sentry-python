from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.utils import event_from_exception

try:
    from typing import Dict, Any
    from pydantic import BaseModel
    from starlite import Starlite, State, HTTPException
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlite.types import Scope, ASGIApp
except ImportError:
    raise DidNotEnable("Starlite is not installed")


class SentryStarliteASGIMiddleware(SentryAsgiMiddleware):
    def __init__(self, app: ASGIApp):
        self.app = app
        self.transaction_style = ("endpoint",)
        self.mechanism_type = ("asgi",)
        self.__call__ = self._run_asgi3


class StarliteIntegraiton(Integration):
    identifier = "starlite"

    @staticmethod
    def setup_once():
        old__init__ = Starlite.__init__

        def injection_wrapper(self, *args, **kwargs):
            # after exception handlers, see: https://starlite-api.github.io/starlite/usage/0-the-starlite-app/5-application-hooks/#after-exception
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

            # middlewares, see: https://starlite-api.github.io/starlite/usage/7-middleware/0-middleware-intro/
            middleware = kwargs.pop("middleware", [])
            kwargs.update(middleware=[SentryStarliteASGIMiddleware, *middleware])

            old__init__(self, *args, **kwargs)

        Starlite.__init__ = injection_wrapper


def retrieve_user_from_scope(scope: Scope) -> Dict[str, Any]:
    user_dict = {}
    if "user" not in scope and not _should_send_default_pii():
        return user_dict

    scope_user = scope.get("user")
    if isinstance(scope_user, dict):
        return scope_user
    elif isinstance(scope_user, BaseModel):
        return scope_user.dict()
    elif hasattr(scope_user, "as_dict"):
        return scope_user.as_dict()  # Dict[str, Any]
    return user_dict


def exception_handler(exc: Exception, scope: Scope, _: State):
    hub = Hub.current
    if hub.get_integration(StarliteIntegraiton) is None:
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
            mechanism={"type": StarliteIntegraiton.identifier, "handled": True},
        )

        hub.capture_event(event, hint=hint)

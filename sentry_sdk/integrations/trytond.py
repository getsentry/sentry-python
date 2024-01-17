import typing as t

import sentry_sdk.hub
import sentry_sdk.utils
import sentry_sdk.integrations
import sentry_sdk.integrations.wsgi
import sentry_sdk._types

from trytond.exceptions import TrytonException  # type: ignore
from trytond.protocols.wrappers import Request  # type: ignore
from trytond.protocols.wrappers import Response

if sentry_sdk._types.TYPE_CHECKING:
    from trytond.wsgi import TrytondWSGI  # type: ignore


ErrorHandler = t.Callable[["TrytondWSGI", Request, Exception], t.Optional[Response]]

# TODO: trytond-worker, trytond-cron and trytond-admin intergations


class TrytondWSGIIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond_wsgi"

    def __init__(self):  # type: () -> None
        pass

    @staticmethod
    def setup_once():  # type: () -> None
        # Lazy import for potential setup as a trytond middleware
        from trytond.wsgi import app
        from trytond.wsgi import TrytondWSGI

        app_dispatch_request = app.dispatch_request

        def error_handler(e: Exception) -> None:
            hub = sentry_sdk.hub.Hub.current

            if hub.get_integration(TrytondWSGIIntegration) is None:
                return
            elif isinstance(e, TrytonException):
                return
            else:
                # If an integration is there, a client has to be there.
                client = hub.client  # type: t.Any
                event, hint = sentry_sdk.utils.event_from_exception(
                    e,
                    client_options=client.options,
                    mechanism={"type": "trytond", "handled": False},
                )
                hub.capture_event(event, hint=hint)

        # Expected error handlers signature was changed
        # when the error_handler decorator was introduced
        # in Tryton-5.4
        if hasattr(app, "error_handler"):
            error_handler_decorator: t.Callable[[ErrorHandler], ErrorHandler] = app.error_handler

            @error_handler_decorator
            def _(app: TrytondWSGI, request: Request, e: Exception) -> None:
                error_handler(e)

        else:
            app.error_handlers.append(error_handler)

        def tracing_app_dispatch_request(request: Request) -> t.Any:
            username = None
            if request.authorization:
                username = request.authorization.username
            elif request.rpc_method == "common.db.login":
                try:
                    (username, *_) = request.rpc_params
                except (TypeError, ValueError):
                    pass
            if username:
                sentry_sdk.set_user({"username": username})

            if request.rpc_method:

                def trace_dispatch(req: Request) -> t.Any:
                    with sentry_sdk.configure_scope() as scope:
                        scope.set_transaction_name(request.rpc_method)
                        return app_dispatch_request(req)

                dispatch = trace_dispatch
            else:
                # TODO: Figure out non-RPC "res.user.application" requests (but
                # sentry_sdk.integrations.wsgi.SentryWsgiMiddleware might do the job)
                dispatch = app_dispatch_request

            return dispatch(request)

        app.wsgi_app = sentry_sdk.integrations.wsgi.SentryWsgiMiddleware(app.wsgi_app)
        app.dispatch_request = tracing_app_dispatch_request

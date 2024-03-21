import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.utils import event_from_exception

from trytond.exceptions import TrytonException  # type: ignore
from trytond.wsgi import app  # type: ignore


# TODO: trytond-worker, trytond-cron and trytond-admin intergations


class TrytondWSGIIntegration(Integration):
    identifier = "trytond_wsgi"

    def __init__(self):  # type: () -> None
        pass

    @staticmethod
    def setup_once():  # type: () -> None
        app.wsgi_app = SentryWsgiMiddleware(app.wsgi_app)

        def error_handler(e):  # type: (Exception) -> None
            client = sentry_sdk.get_client()
            if client.get_integration(TrytondWSGIIntegration) is None:
                return
            elif isinstance(e, TrytonException):
                return
            else:
                event, hint = event_from_exception(
                    e,
                    client_options=client.options,
                    mechanism={"type": "trytond", "handled": False},
                )
                sentry_sdk.capture_event(event, hint=hint)

        # Expected error handlers signature was changed
        # when the error_handler decorator was introduced
        # in Tryton-5.4
        if hasattr(app, "error_handler"):

            @app.error_handler
            def _(app, request, e):  # type: ignore
                error_handler(e)

        else:
            app.error_handlers.append(error_handler)

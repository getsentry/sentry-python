import sys

import sentry_sdk
import sentry_sdk.hub
import sentry_sdk.utils
import sentry_sdk.integrations

from trytond.exceptions import TrytonException  # type: ignore
from trytond.wsgi import app  # type: ignore


# TODO: trytond-worker, trytond-cron and trytond-admin intergations


class TrytondWSGIIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond_wsgi"

    def __init__(self):  # type: () -> None
        pass

    @staticmethod
    def setup_once():  # type: () -> None
        def error_handler(e):  # type: (Exception) -> None
            hub = sentry_sdk.hub.Hub.current
            if hub.get_integration(TrytondWSGIIntegration) is None:
                return
            elif isinstance(e, TrytonException):
                return
            else:
                (event, hint) = sentry_sdk.utils.event_from_exception(
                    sys.exc_info()
                )
                hub.capture_event(event, hint=hint)

        # Expected error handlers signature was changed
        # when the error_handler decorator was introduced
        # in Tryton-5.4
        if hasattr(app, "error_handler"):

            @app.error_handler
            def _(app, request, e):  # type: ignore
                error_handler(e)

        else:
            app.error_handlers.append(error_handler)

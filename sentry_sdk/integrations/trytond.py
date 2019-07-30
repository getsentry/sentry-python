
import sentry_sdk
import sentry_sdk.integrations

from trytond.wsgi import app
from trytond.exceptions import UserError as TrytondUserError
from trytond.exceptions import UserWarning as TrytondUserWarning
from trytond.exceptions import ConcurrencyException
from trytond.exceptions import LoginException


def append_err_handler(self, handler):
    self.error_handlers.append(handler)
    return handler


app.append_err_handler = append_err_handler.__get__(app)


# TODO: trytond_cron and trytond_worker intergations
class TrytondWSGIIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond_wsgi"

    def __init__(self, options=None):
        pass

    @staticmethod
    def setup_once():

        @app.append_err_handler
        def error_handler(e):
            if type(e) not in {
                TrytondUserError,
                TrytondUserWarning,
                ConcurrencyException,
                LoginException,
            }:
                sentry_sdk.capture_exception(e)
            return

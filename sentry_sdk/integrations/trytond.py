
import sentry_sdk
import sentry_sdk.integrations

from trytond.wsgi import app


def append_err_handler(self, handler):
    self.error_handlers.append(handler)
    return handler


app.append_err_handler = append_err_handler.__get__(app)


class TrytondIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond"

    def __init__(self, options=None):
        pass

    @staticmethod
    def setup_once():

        @app.append_err_handler
        def error_handler(e):
            event_id = sentry_sdk.capture_exception(e)
            return e

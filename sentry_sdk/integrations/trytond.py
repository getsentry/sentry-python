

from sentry_sdk.integrations import Integration
from sentry_sdk import capture_exception

import sentry_sdk
import sentry_sdk.integrations

from werkzeug.wrappers import Response
from trytond.application import app as application


def append_err_handler(self, handler):
    self.error_handlers.append(handler)
    return handler


def prepend_err_handler(self, handler):
    self.error_handlers.insert(0, handler)
    return handler


# One would expect the TrytondWSGI class to implement such decorators
# but it does not
application.append_err_handler = append_err_handler.__get__(application)
application.prepend_err_handler = prepend_err_handler.__get__(application)


def error_handler(e):
    event_id = sentry_sdk.capture_exception(e)
    return e


class TrytondIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond"

    def __init__(self, options=None):
        pass

    @staticmethod
    def setup_once():
        application.append_err_handler(error_handler)


sentry_sdk.init(
    "dsn",
    integrations=[TrytondIntegration()]
)

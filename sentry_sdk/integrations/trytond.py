
import sentry_sdk
import sentry_sdk.integrations

from werkzeug.wrappers import Response
from trytond.wsgi import app
from trytond.exceptions import TrytonException as TrytondBaseException
from trytond.exceptions import UserError as TrytondUserError
from trytond.protocols.jsonrpc import JSONRequest


def append_err_handler(self, handler):
    self.error_handlers.append(
        handler.__get__(self)
    )
    return handler


app.append_err_handler = append_err_handler.__get__(app)


# TODO: trytond_cron and trytond_worker intergations
class TrytondWSGIIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond_wsgi"

    def __init__(self):
        pass

    @staticmethod
    def setup_once():

        @app.append_err_handler
        def _(self, e):
            if isinstance(e, TrytondBaseException):
                return
            else:
                sentry_sdk.capture_exception(e)


def rpc_error_page(self, e):
    if isinstance(e, TrytondBaseException):
        return
    else:
    _environ = dict()
    request_stub = type('JSONRequestStub', (JSONRequest,), dict(
        accept_mimetypes=[('json', None)],
        parsed_data=dict(
            id=None,  # FIXME: The client won't trust this response because
                      # the parameter id won't match the one it crafted
                      # for the request. We must gain access to the
                      # original request for error handlers
            method=None,
            params=None
        ),
    ))(_environ)
    event_id = sentry_sdk.last_event_id()
    data = TrytondUserError(
        str(event_id),
        str(e)
    )
    return self.make_response(
        request_stub,
        data
    )

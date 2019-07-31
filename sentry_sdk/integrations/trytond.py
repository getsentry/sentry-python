
import sentry_sdk
import sentry_sdk.integrations

from werkzeug.wrappers import Response
from trytond.wsgi import app
from trytond.exceptions import UserError as TrytondUserError
from trytond.exceptions import UserWarning as TrytondUserWarning
from trytond.exceptions import ConcurrencyException
from trytond.exceptions import LoginException


def append_err_handler(self, handler):
    self.error_handlers.append(handler)
    return handler


app.append_err_handler = append_err_handler.__get__(app)


def noop(e): pass


def asdf(self, e):
    class request:
        accept_mimetypes = [('json', None)]
        parsed_data = dict()
    event_id = sentry_sdk.last_event_id()
    data = TrytondUserError(
        str(event_id),
        str(e)
    )
    data = dict(
        id=3,
        event_id=event_id
    )
    return self.make_response(
        request,
        data)
    # return Response(str(event_id))


# TODO: trytond_cron and trytond_worker intergations
class TrytondWSGIIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond_wsgi"

    def __init__(self, *, error_responder=noop):
        pass

    @staticmethod
    def setup_once():

        bound_error_responder = asdf.__get__(app)
        @app.append_err_handler
        def error_handler(e):
            import pudb; pudb.set_trace()
            if isinstance(e, (
                    TrytondUserError,
                    TrytondUserWarning,
                    ConcurrencyException,
                    LoginException)):
                return
            else:
                event_id = sentry_sdk.capture_exception(e)
                return bound_error_responder(e)

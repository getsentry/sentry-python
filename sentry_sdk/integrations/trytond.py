
import logging
import sentry_sdk
import sentry_sdk.integrations
import sentry_sdk.integrations.logging

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
                id=None,
                # FIXME: A Trtyon RPC client won't trust this response
                # because the `id` parameter won't match the one it
                # crafted for the request. We must gain access to the
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


class TrytondSentryHandler(logging.NullHandler):

    def __init__(self, dsn, ignore=tuple()):
        super(TrytondSentryHandler, self).__init__()
        # There is no clean way to run a static configuration code block
        # when running the trytond-cron binary without crafting a whole
        # new binary that wraps it.
        # (See https://github.com/trytonus/trytond-sentry/blob/master/bin/trytond_sentry)
        # This is a workaround to provide a behaviour
        # similar to the old raven package
        sentry_sdk.init(dsn)
        # Also, there is no need to inherit
        # sentry_sdk.integrations.logging.EventHandler because
        # sentry_sdk.init will already install the default
        # LoggingIntegration that will already spy on any
        # logger.error call except for the following
        for logger in ignore:
            sentry_sdk.integrations.logging.ignore_logger(logger)
        # That is: there is no actual TrytondCronIntegration to be written
        # but this is needed so as to inject a sentry_sdk.init call

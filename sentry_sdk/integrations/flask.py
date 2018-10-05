from __future__ import absolute_import

import weakref

from sentry_sdk import Hub, configure_scope
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi import RequestExtractor, run_wsgi_app

try:
    import flask_login
except ImportError:
    flask_login = None

from flask import Flask, _request_ctx_stack, _app_ctx_stack
from flask.signals import (
    appcontext_pushed,
    appcontext_tearing_down,
    got_request_exception,
    request_started,
)


class FlaskIntegration(Integration):
    identifier = "flask"

    transaction_style = None

    def __init__(self, transaction_style="endpoint"):
        TRANSACTION_STYLE_VALUES = ("endpoint", "url")
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    def install(self):
        appcontext_pushed.connect(self._push_appctx)
        appcontext_tearing_down.connect(self._pop_appctx)
        request_started.connect(self._request_started)
        got_request_exception.connect(_capture_exception)

        old_app = Flask.__call__

        def sentry_patched_wsgi_app(self, environ, start_response):
            return run_wsgi_app(
                lambda *a, **kw: old_app(self, *a, **kw), environ, start_response
            )

        Flask.__call__ = sentry_patched_wsgi_app

    def _push_appctx(self, *args, **kwargs):
        # always want to push scope regardless of whether WSGI app might already
        # have (not the case for CLI for example)
        hub = Hub.current
        hub.push_scope()

    def _pop_appctx(self, *args, **kwargs):
        Hub.current.pop_scope_unsafe()

    def _request_started(self, sender, **kwargs):
        weak_request = weakref.ref(_request_ctx_stack.top.request)
        app = _app_ctx_stack.top.app
        with configure_scope() as scope:
            scope.add_event_processor(
                self._make_request_event_processor(app, weak_request)
            )

    def _make_request_event_processor(self, app, weak_request):
        def inner(event, hint):
            request = weak_request()

            # if the request is gone we are fine not logging the data from
            # it.  This might happen if the processor is pushed away to
            # another thread.
            if request is None:
                return event

            if "transaction" not in event:
                try:
                    if self.transaction_style == "endpoint":
                        event["transaction"] = request.url_rule.endpoint
                    elif self.transaction_style == "url":
                        event["transaction"] = request.url_rule.rule
                except Exception:
                    pass

            with capture_internal_exceptions():
                FlaskRequestExtractor(request).extract_into_event(event)

            if _should_send_default_pii():
                with capture_internal_exceptions():
                    _add_user_to_event(event)

            return event

        return inner


class FlaskRequestExtractor(RequestExtractor):
    def url(self):
        return "%s://%s%s" % (self.request.scheme, self.request.host, self.request.path)

    def env(self):
        return self.request.environ

    def cookies(self):
        return self.request.cookies

    def raw_data(self):
        return self.request.data

    def form(self):
        return self.request.form

    def files(self):
        return self.request.files

    def size_of_file(self, file):
        return file.content_length


def _capture_exception(sender, exception, **kwargs):
    hub = Hub.current
    event, hint = event_from_exception(
        exception,
        client_options=hub.client.options,
        mechanism={"type": "flask", "handled": False},
    )

    hub.capture_event(event, hint=hint)


def _add_user_to_event(event):
    if flask_login is None:
        return

    user = flask_login.current_user
    if user is None:
        return

    with capture_internal_exceptions():
        # Access this object as late as possible as accessing the user
        # is relatively costly

        user_info = event.setdefault("user", {})

        if "id" not in user_info:
            try:
                user_info["id"] = user.get_id()
                # TODO: more configurable user attrs here
            except AttributeError:
                # might happen if:
                # - flask_login could not be imported
                # - flask_login is not configured
                # - no user is logged in
                pass

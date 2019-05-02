from __future__ import absolute_import

import weakref

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.integrations._wsgi_common import RequestExtractor


try:
    import flask_login  # type: ignore
except ImportError:
    flask_login = None

from flask import Flask, _request_ctx_stack, _app_ctx_stack  # type: ignore
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

    @staticmethod
    def setup_once():

        appcontext_pushed.connect(_push_appctx)
        appcontext_tearing_down.connect(_pop_appctx)
        request_started.connect(_request_started)
        got_request_exception.connect(_capture_exception)

        old_app = Flask.__call__

        def sentry_patched_wsgi_app(self, environ, start_response):

            if Hub.current.get_integration(FlaskIntegration) is None:
                return old_app(self, environ, start_response)

            return SentryWsgiMiddleware(lambda *a, **kw: old_app(self, *a, **kw))(
                environ, start_response
            )

        Flask.__call__ = sentry_patched_wsgi_app


def _push_appctx(*args, **kwargs):

    hub = Hub.current
    if hub.get_integration(FlaskIntegration) is not None:
        # always want to push scope regardless of whether WSGI app might already
        # have (not the case for CLI for example)
        scope_manager = hub.push_scope()
        scope_manager.__enter__()
        _app_ctx_stack.top.sentry_sdk_scope_manager = scope_manager
        with hub.configure_scope() as scope:
            scope._name = "flask"


def _pop_appctx(*args, **kwargs):

    scope_manager = getattr(_app_ctx_stack.top, "sentry_sdk_scope_manager", None)
    if scope_manager is not None:
        scope_manager.__exit__(None, None, None)


def _request_started(sender, **kwargs):

    hub = Hub.current
    integration = hub.get_integration(FlaskIntegration)
    if integration is None:
        return

    app = _app_ctx_stack.top.app
    with hub.configure_scope() as scope:
        request = _request_ctx_stack.top.request
        weak_request = weakref.ref(request)
        scope.add_event_processor(
            _make_request_event_processor(app, weak_request, integration)
        )


class FlaskRequestExtractor(RequestExtractor):
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

    def is_json(self):
        return self.request.is_json

    def json(self):
        return self.request.get_json()

    def size_of_file(self, file):

        return file.content_length


def _make_request_event_processor(app, weak_request, integration):
    def inner(event, hint):

        request = weak_request()

        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        if request is None:
            return event

        try:
            if integration.transaction_style == "endpoint":
                event["transaction"] = request.url_rule.endpoint
            elif integration.transaction_style == "url":
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


def _capture_exception(sender, exception, **kwargs):

    hub = Hub.current
    if hub.get_integration(FlaskIntegration) is None:
        return
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

        try:
            user_info["id"] = user.get_id()
            # TODO: more configurable user attrs here
        except AttributeError:
            # might happen if:
            # - flask_login could not be imported
            # - flask_login is not configured
            # - no user is logged in
            pass

        # The following attribute accesses are ineffective for the general
        # Flask-Login case, because the User interface of Flask-Login does not
        # care about anything but the ID. However, Flask-User (based on
        # Flask-Login) documents a few optional extra attributes.
        #
        # https://github.com/lingthio/Flask-User/blob/a379fa0a281789618c484b459cb41236779b95b1/docs/source/data_models.rst#fixed-data-model-property-names

        try:
            user_info["email"] = user_info["username"] = user.email
        except Exception:
            pass

        try:
            user_info["username"] = user.username
        except Exception:
            pass

from __future__ import absolute_import

import weakref

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

    @classmethod
    def install(cls):
        appcontext_pushed.connect(cls._push_appctx)
        appcontext_tearing_down.connect(cls._pop_appctx)
        request_started.connect(cls._request_started)
        got_request_exception.connect(cls._capture_exception)

        old_app = Flask.__call__

        def sentry_patched_wsgi_app(self, environ, start_response):
            if not cls.is_active:
                return old_app(self, environ, start_response)

            return run_wsgi_app(
                lambda *a, **kw: old_app(self, *a, **kw), environ, start_response
            )

        Flask.__call__ = sentry_patched_wsgi_app

    @classmethod
    def _push_appctx(cls, *args, **kwargs):
        atch = cls.current_attachment
        if atch is None:
            return
        # always want to push scope regardless of whether WSGI app might already
        # have (not the case for CLI for example)
        atch.hub.push_scope()

    @classmethod
    def _pop_appctx(cls, *args, **kwargs):
        atch = cls.current_attachment
        if atch is not None:
            atch.hub.pop_scope_unsafe()

    @classmethod
    def _request_started(cls, sender, **kwargs):
        atch = cls.current_attachment
        if atch is None:
            return

        weak_request = weakref.ref(_request_ctx_stack.top.request)
        app = _app_ctx_stack.top.app
        with atch.hub.configure_scope() as scope:
            scope.add_event_processor(_make_request_event_processor(app, weak_request))

    @classmethod
    def _capture_exception(cls, sender, exception, **kwargs):
        atch = cls.current_attachment
        if atch is None:
            return
        event, hint = event_from_exception(
            exception,
            with_locals=atch.client.options["with_locals"],
            mechanism={"type": "flask", "handled": False},
        )
        atch.hub.capture_event(event, hint=hint)


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


def _make_request_event_processor(app, weak_request):
    def inner(event, hint):
        request = weak_request()

        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        if request is None:
            return event

        if "transaction" not in event:
            try:
                event["transaction"] = request.url_rule.endpoint
            except Exception:
                pass

        with capture_internal_exceptions():
            FlaskRequestExtractor(request).extract_into_event(event)

        if _should_send_default_pii():
            with capture_internal_exceptions():
                _add_user_to_event(event)

        return event

    return inner


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

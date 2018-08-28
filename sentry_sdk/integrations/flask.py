from __future__ import absolute_import

import weakref

from sentry_sdk import capture_exception, get_current_hub
from sentry_sdk.hub import _internal_exceptions, _should_send_default_pii
from ._wsgi import RequestExtractor, run_wsgi_app
from . import Integration

try:
    from flask_login import current_user as current_user_proxy
except ImportError:
    current_user_proxy = None

from flask import Flask, _request_ctx_stack, _app_ctx_stack
from flask.signals import (
    appcontext_pushed,
    appcontext_tearing_down,
    got_request_exception,
)


class FlaskIntegration(Integration):
    identifier = "flask"

    def __init__(self):
        pass

    def install(self, client):
        appcontext_pushed.connect(_push_appctx)
        appcontext_tearing_down.connect(_pop_appctx)
        got_request_exception.connect(_capture_exception)

        old_app = Flask.__call__

        def sentry_patched_wsgi_app(self, environ, start_response):
            return run_wsgi_app(
                lambda *a, **kw: old_app(self, *a, **kw), environ, start_response
            )

        Flask.__call__ = sentry_patched_wsgi_app


def _push_appctx(*args, **kwargs):
    # always want to push scope regardless of whether WSGI app might already
    # have (not the case for CLI for example)
    get_current_hub().push_scope()

    app = getattr(_app_ctx_stack.top, "app", None)

    try:
        weak_request = weakref.ref(_request_ctx_stack.top.request)
    except AttributeError:
        weak_request = lambda: None

    try:
        weak_user = weakref.ref(current_user_proxy._get_current_object())
    except (AttributeError, RuntimeError, TypeError):
        weak_user = lambda: None

    get_current_hub().add_event_processor(
        lambda: _make_event_processor(app, weak_request, weak_user)
    )


def _pop_appctx(*args, **kwargs):
    get_current_hub().pop_scope_unsafe()


def _capture_exception(sender, exception, **kwargs):
    capture_exception(exception)


def _make_event_processor(app, weak_request, weak_user):
    client_options = get_current_hub().client.options

    def event_processor(event):
        request = weak_request()

        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        if request is not None:
            if "transaction" not in event:
                try:
                    event["transaction"] = request.url_rule.endpoint
                except Exception:
                    pass

            with _internal_exceptions():
                FlaskRequestExtractor(request).extract_into_event(event, client_options)

        if _should_send_default_pii():
            with _internal_exceptions():
                _set_user_info(request, weak_user(), event)

        with _internal_exceptions():
            _process_frames(app, event)

    return event_processor


def _process_frames(app, event):
    for frame in event.iter_frames():
        if "in_app" in frame:
            continue
        module = frame.get("module")
        if not module:
            continue

        if module == "flask" or module.startswith("flask."):
            frame["in_app"] = False
        elif app and (
            module.startswith("%s." % app.import_name) or module == app.import_name
        ):
            frame["in_app"] = True


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


def _set_user_info(request, user, event):
    if "user" in event:
        return

    if request:
        try:
            ip_address = request.access_route[0]
        except IndexError:
            ip_address = request.remote_addr
    else:
        ip_address = None

    user_info = {"id": None, "ip_address": ip_address}

    try:
        user_info["id"] = user.get_id()
        # TODO: more configurable user attrs here
    except AttributeError:
        # might happen if:
        # - flask_login could not be imported
        # - flask_login is not configured
        # - no user is logged in
        pass

    event["user"] = user_info

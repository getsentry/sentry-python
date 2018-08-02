from __future__ import absolute_import

from sentry_sdk import capture_exception, get_current_hub
from sentry_sdk.hub import _internal_exceptions, _should_send_default_pii
from ._wsgi import RequestExtractor
from . import Integration

try:
    from flask_login import current_user
except ImportError:
    current_user = None

from flask import _request_ctx_stack, _app_ctx_stack
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


def _push_appctx(*args, **kwargs):
    get_current_hub().push_scope()
    get_current_hub().add_event_processor(_make_event_processor)


def _pop_appctx(*args, **kwargs):
    get_current_hub().pop_scope_unsafe()


def _capture_exception(sender, exception, **kwargs):
    capture_exception(exception)


def _make_event_processor():
    request = getattr(_request_ctx_stack.top, "request", None)
    app = getattr(_app_ctx_stack.top, "app", None)
    client_options = get_current_hub().client.options

    def event_processor(event):
        if request:
            if "transaction" not in event:
                with _internal_exceptions():
                    event["transaction"] = request.url_rule.endpoint

            with _internal_exceptions():
                FlaskRequestExtractor(request).extract_into_event(event, client_options)

        if _should_send_default_pii():
            with _internal_exceptions():
                _set_user_info(request, event)

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
    @property
    def url(self):
        return "%s://%s%s" % (self.request.scheme, self.request.host, self.request.path)

    @property
    def env(self):
        return self.request.environ

    @property
    def cookies(self):
        return self.request.cookies

    @property
    def raw_data(self):
        return self.request.data

    @property
    def form(self):
        return self.request.form

    @property
    def files(self):
        return self.request.files

    def size_of_file(self, file):
        return file.content_length


def _set_user_info(request, event):
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
        user_info["id"] = current_user.get_id()
        # TODO: more configurable user attrs here
    except AttributeError:
        # might happen if:
        # - flask_login could not be imported
        # - flask_login is not configured
        # - no user is logged in
        pass

    event["user"] = user_info

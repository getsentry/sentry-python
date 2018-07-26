from __future__ import absolute_import

from threading import Lock

from sentry_sdk import capture_exception, configure_scope, get_current_hub, init
from ._wsgi import RequestExtractor

try:
    from flask_login import current_user
except ImportError:
    current_user = None

from flask import request, _app_ctx_stack
from flask.signals import appcontext_pushed, appcontext_tearing_down, got_request_exception


_installer_lock = Lock()
_installed = False


def install(client):
    global _installed
    with _installer_lock:
        if _installed:
            return

        appcontext_pushed.connect(_push_appctx)
        appcontext_tearing_down.connect(_pop_appctx)
        got_request_exception.connect(_capture_exception)

        _installed = True

def _push_appctx(*args, **kwargs):
    get_current_hub().push_scope()

    with configure_scope() as scope:
        get_current_hub().add_event_processor(lambda: _event_processor)

def _pop_appctx(*args, **kwargs):
    get_current_hub().pop_scope_unsafe()

def _capture_exception(sender, exception, **kwargs):
    capture_exception(exception)


def _event_processor(event):
    if "transaction" not in event:
        try:
            event["transaction"] = request.url_rule.endpoint
        except Exception:
            get_current_hub().capture_internal_exception()

    try:
        FlaskRequestExtractor(request).extract_into_event(event)
    except Exception:
        get_current_hub().capture_internal_exception()

    try:
        _set_user_info(event)
    except Exception:
        get_current_hub().capture_internal_exception()


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
        return request.files

    def size_of_file(self, file):
        return file.content_length


def _set_user_info(event):
    if "user" in event:
        return

    try:
        ip_address = request.access_route[0]
    except IndexError:
        ip_address = request.remote_addr

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

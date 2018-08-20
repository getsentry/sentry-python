from __future__ import absolute_import

from django import VERSION as DJANGO_VERSION
from django.core import signals

try:
    from django.urls import resolve
except ImportError:
    from django.core.urlresolvers import resolve

from sentry_sdk import get_current_hub, capture_exception
from sentry_sdk.hub import _internal_exceptions, _should_send_default_pii
from ._wsgi import RequestExtractor, get_client_ip, run_wsgi_app
from . import Integration


if DJANGO_VERSION < (1, 10):

    def is_authenticated(request_user):
        return request_user.is_authenticated()


else:

    def is_authenticated(request_user):
        return request_user.is_authenticated


class DjangoIntegration(Integration):
    identifier = "django"

    def __init__(self):
        pass

    def install(self, client):
        # Patch in our custom middleware.

        from django.core.handlers.wsgi import WSGIHandler

        old_app = WSGIHandler.__call__

        def sentry_patched_wsgi_handler(self, environ, start_response):
            return run_wsgi_app(
                lambda *a, **kw: old_app(self, *a, **kw), environ, start_response
            )

        WSGIHandler.__call__ = sentry_patched_wsgi_handler

        # patch get_response, because at that point we have the Django request
        # object

        from django.core.handlers.base import BaseHandler

        old_get_response = BaseHandler.get_response
        make_event_processor = self._make_event_processor

        def sentry_patched_get_response(self, request):
            get_current_hub().add_event_processor(lambda: make_event_processor(request))

            return old_get_response(self, request)

        BaseHandler.get_response = sentry_patched_get_response

        signals.got_request_exception.connect(_got_request_exception)

    def _make_event_processor(self, request):
        client_options = get_current_hub().client.options

        def processor(event):
            if "transaction" not in event:
                try:
                    event["transaction"] = resolve(request.path).func.__name__
                except Exception:
                    pass

            with _internal_exceptions():
                DjangoRequestExtractor(request).extract_into_event(
                    event, client_options
                )

            if _should_send_default_pii():
                with _internal_exceptions():
                    _set_user_info(request, event)

            with _internal_exceptions():
                _process_frames(event)

        return processor


def _process_frames(event):
    for frame in event.iter_frames():
        if "in_app" in frame:
            continue

        module = frame.get("module")
        if not module:
            continue
        if module == "django" or module.startswith("django."):
            frame["in_app"] = False


def _got_request_exception(request=None, **kwargs):
    capture_exception()


class DjangoRequestExtractor(RequestExtractor):
    def url(self):
        return self.request.build_absolute_uri(self.request.path)

    def env(self):
        return self.request.META

    def cookies(self):
        return self.request.COOKIES

    def raw_data(self):
        return self.request.body

    def form(self):
        return self.request.POST

    def files(self):
        return self.request.FILES

    def size_of_file(self, file):
        return file.size


def _set_user_info(request, event):
    if "user" in event:
        return

    event["user"] = user_info = {"ip_address": get_client_ip(request.META)}

    user = getattr(request, "user", None)

    if user is None or not is_authenticated(user):
        return

    try:
        user_info["email"] = user.email
    except Exception:
        pass

    try:
        user_info["username"] = user.get_username()
    except Exception:
        pass

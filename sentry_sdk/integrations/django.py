from __future__ import absolute_import

import sys
import weakref

from django import VERSION as DJANGO_VERSION
from django.core import signals

try:
    from django.urls import resolve
except ImportError:
    from django.core.urlresolvers import resolve

from sentry_sdk import Hub, configure_scope
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi import RequestExtractor, run_wsgi_app


if DJANGO_VERSION < (1, 10):

    def is_authenticated(request_user):
        return request_user.is_authenticated()


else:

    def is_authenticated(request_user):
        return request_user.is_authenticated


class DjangoIntegration(Integration):
    identifier = "django"

    def install(self):
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

        def sentry_patched_get_response(self, request):
            with configure_scope() as scope:
                scope.add_event_processor(_make_event_processor(weakref.ref(request)))
            return old_get_response(self, request)

        BaseHandler.get_response = sentry_patched_get_response

        signals.got_request_exception.connect(_got_request_exception)


def _make_event_processor(weak_request):
    def event_processor(event, hint):
        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        request = weak_request()
        if request is None:
            return event

        if "transaction" not in event:
            try:
                event["transaction"] = resolve(request.path).func.__name__
            except Exception:
                pass

        with capture_internal_exceptions():
            DjangoRequestExtractor(request).extract_into_event(event)

        if _should_send_default_pii():
            with capture_internal_exceptions():
                _set_user_info(request, event)

        return event

    return event_processor


def _got_request_exception(request=None, **kwargs):
    hub = Hub.current
    event, hint = event_from_exception(
        sys.exc_info(),
        with_locals=hub.client.options["with_locals"],
        mechanism={"type": "django", "handled": False},
    )

    hub.capture_event(event, hint=hint)


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
    user_info = event.setdefault("user", {})

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

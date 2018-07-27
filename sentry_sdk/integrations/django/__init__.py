from __future__ import absolute_import

from threading import Lock

from django.conf import settings
from django.core import signals

try:
    from django.urls import resolve
except ImportError:
    from django.core.urlresolvers import resolve

from sentry_sdk import get_current_hub, configure_scope, capture_exception
from sentry_sdk.hub import _internal_exceptions
from .._wsgi import RequestExtractor


try:
    # Django >= 1.10
    from django.utils.deprecation import MiddlewareMixin
except ImportError:
    # Not required for Django <= 1.9, see:
    # https://docs.djangoproject.com/en/1.10/topics/http/middleware/#upgrading-pre-django-1-10-style-middleware
    MiddlewareMixin = object


def _get_transaction_from_request(request):
    return resolve(request.path).func.__name__


# request_started (or any other signal) cannot be used because the request is
# not yet available
class SentryMiddleware(MiddlewareMixin):
    def process_request(self, request):
        with _internal_exceptions():
            get_current_hub().push_scope()

            get_current_hub().add_event_processor(
                lambda: self.make_event_processor(request)
            )

            with configure_scope() as scope:
                scope.transaction = _get_transaction_from_request(request)

    def make_event_processor(self, request):
        def processor(event):
            with _internal_exceptions():
                DjangoRequestExtractor(request).extract_into_event(event)

            # TODO: user info

        return processor


class DjangoRequestExtractor(RequestExtractor):
    @property
    def url(self):
        return self.request.build_absolute_uri(self.request.path)

    @property
    def env(self):
        return self.request.META

    @property
    def cookies(self):
        return self.request.COOKIES

    @property
    def raw_data(self):
        return self.request.body

    @property
    def form(self):
        return self.request.POST

    @property
    def files(self):
        return self.request.FILES

    def size_of_file(self, file):
        return file.size


def _request_finished(*args, **kwargs):
    get_current_hub().pop_scope_unsafe()


def _got_request_exception(request=None, **kwargs):
    capture_exception()


MIDDLEWARE_NAME = __name__ + ".SentryMiddleware"

_installer_lock = Lock()
_installed = False


def install(client):
    global _installed
    with _installer_lock:
        if _installed:
            return

        _install_impl()
        _installed = True


def _install_impl():
    old_getattr = settings.__getattr__

    # When `sentry_sdk.init` is called, the settings object may or may not be
    # initialized. So we need to lazily merge the array values.

    def sentry_patched_getattr(self, key):
        if key in ("MIDDLEWARE", "MIDDLEWARE_CLASSES"):
            try:
                rv = old_getattr(key)
            except AttributeError:
                rv = []

            rv = type(rv)([MIDDLEWARE_NAME]) + rv
        elif key == "INSTALLED_APPS":
            try:
                rv = old_getattr(key)
            except AttributeError:
                rv = []

            rv = type(rv)([__name__]) + rv
        else:
            rv = old_getattr(key)

        # fix cache set by old_getattr
        if key in self.__dict__:
            self.__dict__[key] = rv
        return rv

    type(settings).__getattr__ = sentry_patched_getattr

    signals.request_finished.connect(_request_finished)
    signals.got_request_exception.connect(_got_request_exception)

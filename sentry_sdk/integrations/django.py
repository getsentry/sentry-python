from __future__ import absolute_import

from django.core import signals

try:
    from django.urls import resolve
except ImportError:
    from django.core.urlresolvers import resolve

from sentry_sdk import get_current_hub, configure_scope, capture_exception
from sentry_sdk.hub import _internal_exceptions
from ._wsgi import RequestExtractor
from . import Integration


class DjangoIntegration(Integration):
    identifier = 'django'

    def __init__(self):
        pass

    def install(self, client):
        from django.core.handlers.base import BaseHandler

        make_event_processor = self._make_event_processor

        old_get_response = BaseHandler.get_response

        def sentry_patched_get_response(self, request):
            with get_current_hub().push_scope():
                get_current_hub().add_event_processor(
                    lambda: make_event_processor(request)
                )

                with configure_scope() as scope:
                    scope.transaction = resolve(request.path).func.__name__

                return old_get_response(self, request)

        BaseHandler.get_response = sentry_patched_get_response

        signals.got_request_exception.connect(_got_request_exception)

    def _make_event_processor(self, request):
        def processor(event):
            with _internal_exceptions():
                DjangoRequestExtractor(request).extract_into_event(event)

            # TODO: user info

        return processor


def _got_request_exception(request=None, **kwargs):
    capture_exception()



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

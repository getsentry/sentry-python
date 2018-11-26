from __future__ import absolute_import

import os
import sys
import weakref

from pyramid.httpexceptions import HTTPException

from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk._compat import reraise

from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi_common import RequestExtractor
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware


class PyramidIntegration(Integration):
    identifier = "pyramid"

    transaction_style = None

    def __init__(self, transaction_style="route_name"):
        TRANSACTION_STYLE_VALUES = ("route_name", "route_pattern")
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    @staticmethod
    def setup_once():
        from pyramid.router import Router

        old_handle_request = Router.handle_request

        def sentry_patched_handle_request(self, request, *args, **kwargs):
            hub = Hub.current
            integration = hub.get_integration(PyramidIntegration)
            if integration is None:
                return old_handle_request(self, request, *args, **kwargs)

            with hub.configure_scope() as scope:
                scope.add_event_processor(
                    _make_event_processor(weakref.ref(request), integration)
                )

            try:
                return old_handle_request(self, request, *args, **kwargs)
            except Exception:
                exc_info = sys.exc_info()
                _capture_exception(exc_info)
                reraise(*exc_info)

        Router.handle_request = sentry_patched_handle_request

        old_wsgi_call = Router.__call__

        def sentry_patched_wsgi_call(self, environ, start_response):
            hub = Hub.current
            integration = hub.get_integration(PyramidIntegration)
            if integration is None:
                return old_wsgi_call(self, environ, start_response)

            return SentryWsgiMiddleware(lambda *a, **kw: old_wsgi_call(self, *a, **kw))(
                environ, start_response
            )

        Router.__call__ = sentry_patched_wsgi_call


def _capture_exception(exc_info, **kwargs):
    if issubclass(exc_info[0], HTTPException):
        return
    hub = Hub.current
    if hub.get_integration(PyramidIntegration) is None:
        return
    event, hint = event_from_exception(
        exc_info,
        client_options=hub.client.options,
        mechanism={"type": "pyramid", "handled": False},
    )

    hub.capture_event(event, hint=hint)


class PyramidRequestExtractor(RequestExtractor):
    def url(self):
        return self.request.path_url

    def env(self):
        return self.request.environ

    def cookies(self):
        return self.request.cookies

    def raw_data(self):
        return self.request.text

    def form(self):
        return {
            key: value
            for key, value in self.request.POST.items()
            if not getattr(value, "filename", None)
        }

    def files(self):
        return {
            key: value
            for key, value in self.request.POST.items()
            if getattr(value, "filename", None)
        }

    def size_of_file(self, postdata):
        file = postdata.file
        try:
            return os.fstat(file.fileno()).st_size
        except Exception:
            return 0


def _make_event_processor(weak_request, integration):
    def event_processor(event, hint):
        request = weak_request()
        if request is None:
            return event

        if "transaction" not in event:
            try:
                if integration.transaction_style == "route_name":
                    event["transaction"] = request.matched_route.name
                elif integration.transaction_style == "route_pattern":
                    event["transaction"] = request.matched_route.pattern
            except Exception:
                pass

        with capture_internal_exceptions():
            PyramidRequestExtractor(request).extract_into_event(event)

        if _should_send_default_pii():
            with capture_internal_exceptions():
                user_info = event.setdefault("user", {})
                if "id" not in user_info:
                    user_info["id"] = request.authenticated_userid

        return event

    return event_processor

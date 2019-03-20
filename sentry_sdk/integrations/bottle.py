from __future__ import absolute_import

import types

from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.integrations._wsgi_common import RequestExtractor

if False:
    from sentry_sdk.integrations.wsgi import _ScopedResponse
    from typing import Any
    from typing import Dict
    from typing import Callable
    from typing import Optional
    from bottle import FileUpload, FormsDict, LocalRequest  # type: ignore

from bottle import Bottle, request as bottle_request  # type: ignore


class BottleIntegration(Integration):
    identifier = "bottle"

    transaction_style = None

    def __init__(self, transaction_style="endpoint"):
        # type: (str) -> None
        TRANSACTION_STYLE_VALUES = ("endpoint", "url")
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    @staticmethod
    def setup_once():
        # type: () -> None

        old_app = Bottle.__call__

        def sentry_patched_wsgi_app(self, environ, start_response):
            # type: (Any, Dict[str, str], Callable) -> _ScopedResponse

            hub = Hub.current
            integration = hub.get_integration(BottleIntegration)
            if integration is None:
                return old_app(self, environ, start_response)

            # monkey patch method self(Bottle).router.match -> (route, args)
            # to monkey patch route.call
            old_match = self.router.match

            def patched_match(self, *args, **kwargs):
                route, route_args = old_match(*args, **kwargs)
                old_call = route.call

                def patched_call(self, *args, **kwargs):
                    try:
                        old_call(*args, **kwargs)
                    except Exception as exception:
                        hub = Hub.current
                        event, hint = event_from_exception(
                            exception,
                            client_options=hub.client.options,
                            mechanism={"type": "bottle", "handled": self.app.catchall},
                        )
                        hub.capture_event(event, hint=hint)
                        raise exception

                route.call = types.MethodType(patched_call, route)
                return route, route_args

            self.router.match = types.MethodType(patched_match, self.route)

            # monkey patch method self(Bottle)._handle
            old_handle = self._handle

            def _patched_handle(self, environ):
                hub = Hub.current
                # create new scope
                scope_manager = hub.push_scope()

                with scope_manager:
                    app = self
                    with hub.configure_scope() as scope:
                        scope._name = "bottle"
                        scope.add_event_processor(
                            _make_request_event_processor(
                                app, bottle_request, integration
                            )
                        )
                    res = old_handle(environ)

                # scope cleanup
                return res

            self._handle = types.MethodType(_patched_handle, self)

            return SentryWsgiMiddleware(lambda *a, **kw: old_app(self, *a, **kw))(
                environ, start_response
            )

        Bottle.__call__ = sentry_patched_wsgi_app  # type: ignore


class BottleRequestExtractor(RequestExtractor):
    def env(self):
        # type: () -> Dict[str, str]
        return self.request.environ

    def cookies(self):
        # type: () -> Dict[str, str]
        return self.request.cookies

    def raw_data(self):
        # type: () -> bytes
        return self.request.body.read()

    def form(self):
        # type: () -> FormsDict
        if self.is_json():
            return None
        return self.request.forms.decode()

    def files(self):
        # type: () -> Optional[Dict[str, str]]
        if self.is_json():
            return None

        return self.request.files

    def size_of_file(self, file):
        # type: (FileUpload) -> int
        return file.content_length


def _make_request_event_processor(app, request, integration):
    # type: (Bottle, LocalRequest, BottleIntegration) -> Callable
    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]

        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        if request is None:
            return event

        try:
            if integration.transaction_style == "endpoint":
                event["transaction"] = request.route.name or getattr(
                    request.route.callback, "func_name", request.route.callback.__name__
                )  # py2 / py3 compatible
            elif integration.transaction_style == "url":
                event["transaction"] = request.route.rule  # type: ignore
        except Exception:
            pass

        with capture_internal_exceptions():
            BottleRequestExtractor(request).extract_into_event(event)

        return event

    return inner

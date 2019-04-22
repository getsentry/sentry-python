import falcon
import falcon.api_helpers
import sentry_sdk.integrations
from sentry_sdk.hub import Hub
from sentry_sdk.integrations._wsgi_common import RequestExtractor
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception

if False:
    from typing import Any
    from typing import Callable
    from typing import Dict


class FalconRequestExtractor(RequestExtractor):
    def env(self):
        return self.request.env

    def cookies(self):
        return self.request.cookies

    def raw_data(self):
        # As request data can only read once we won't make this available
        # to Sentry.
        # TODO(jmagnusson): Figure out if there's a way to support this
        return None

    def form(self):
        return None  # No such concept in Falcon

    def files(self):
        return None  # No such concept in Falcon

    def json(self):
        # We don't touch falcon.Request.media as that can raise an exception
        # on non-JSON requests.
        return self.request._media


class SentryFalconMiddleware(object):
    """Captures exceptions in Falcon requests and send to Sentry"""

    def process_request(self, req, resp, *args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(FalconIntegration)
        if integration is None:
            return

        with hub.configure_scope() as scope:
            scope._name = 'falcon'
            scope.add_event_processor(_make_request_event_processor(req, integration))


class FalconIntegration(Integration):
    identifier = "falcon"

    transaction_style = None

    def __init__(self, transaction_style="uri_template"):
        # type: (str) -> None
        TRANSACTION_STYLE_VALUES = ("uri_template", "path")
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_wsgi_app()
        _patch_handle_exception()
        _patch_prepare_middleware()


def _patch_wsgi_app():
    original_wsgi_app = falcon.API.__call__

    def sentry_patched_wsgi_app(self, env, start_response):
        hub = Hub.current
        integration = hub.get_integration(FalconIntegration)
        if integration is None:
            return original_wsgi_app(self, env, start_response)

        sentry_wrapped = SentryWsgiMiddleware(
            lambda envi, start_resp: original_wsgi_app(self, envi, start_resp)
        )

        return sentry_wrapped(env, start_response)

    falcon.API.__call__ = sentry_patched_wsgi_app


def _patch_handle_exception():
    original_handle_exception = falcon.API._handle_exception

    def sentry_patched_handle_exception(self, *args):
        # NOTE(jmagnusson): falcon 2.0 changed falcon.API._handle_exception
        # method signature from `(ex, req, resp, params)` to
        # `(req, resp, ex, params)`
        if isinstance(args[0], Exception):
            ex = args[0]
        else:
            ex = args[2]

        was_handled = original_handle_exception(self, *args)

        hub = Hub.current
        integration = hub.get_integration(FalconIntegration)

        if integration is not None and not was_handled:
            event, hint = event_from_exception(
                ex,
                client_options=hub.client.options,
                mechanism={"type": "falcon", "handled": False},
            )
            hub.capture_event(event, hint=hint)

        return was_handled

    falcon.API._handle_exception = sentry_patched_handle_exception


def _patch_prepare_middleware():
    original_prepare_middleware = falcon.api_helpers.prepare_middleware

    def sentry_patched_prepare_middleware(
        middleware=None, independent_middleware=False
    ):
        hub = Hub.current
        integration = hub.get_integration(FalconIntegration)
        if integration is not None:
            middleware = [SentryFalconMiddleware()] + (middleware or [])
        return original_prepare_middleware(middleware, independent_middleware)

    falcon.api_helpers.prepare_middleware = sentry_patched_prepare_middleware


def _make_request_event_processor(req, integration):
    # type: (falcon.Request, FalconIntegration) -> Callable

    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        if integration.transaction_style == "uri_template":
            event["transaction"] = req.uri_template
        elif integration.transaction_style == "path":
            event["transaction"] = req.path

        with capture_internal_exceptions():
            FalconRequestExtractor(req).extract_into_event(event)

        return event

    return inner

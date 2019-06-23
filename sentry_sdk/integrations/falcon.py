from __future__ import absolute_import

import falcon  # type: ignore
import falcon.api_helpers  # type: ignore
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi_common import RequestExtractor
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception

MYPY = False
if MYPY:
    from typing import Any
    from typing import Callable
    from typing import Dict


class FalconRequestExtractor(RequestExtractor):
    def env(self):
        return self.request.env

    def cookies(self):
        return self.request.cookies

    def form(self):
        return None  # No such concept in Falcon

    def files(self):
        return None  # No such concept in Falcon

    def raw_data(self):
        # As request data can only be read once we won't make this available
        # to Sentry. Just send back a dummy string in case there was a
        # content length.
        # TODO(jmagnusson): Figure out if there's a way to support this
        content_length = self.content_length()
        if content_length > 0:
            return "[REQUEST_CONTAINING_RAW_DATA]"
        else:
            return None

    def json(self):
        try:
            return self.request.media
        except falcon.errors.HTTPBadRequest:
            # NOTE(jmagnusson): We return `falcon.Request._media` here because
            # falcon 1.4 doesn't do proper type checking in
            # `falcon.Request.media`. This has been fixed in 2.0.
            # Relevant code: https://github.com/falconry/falcon/blob/1.4.1/falcon/request.py#L953
            return self.request._media


class SentryFalconMiddleware(object):
    """Captures exceptions in Falcon requests and send to Sentry"""

    def process_request(self, req, resp, *args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(FalconIntegration)
        if integration is None:
            return

        with hub.configure_scope() as scope:
            scope._name = "falcon"
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

        if integration is not None and not _is_falcon_http_error(ex):
            # If an integration is there, a client has to be there.
            client = hub.client  # type: Any

            event, hint = event_from_exception(
                ex,
                client_options=client.options,
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


def _is_falcon_http_error(ex):
    return isinstance(ex, (falcon.HTTPError, falcon.http_status.HTTPStatus))


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

from __future__ import absolute_import

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk import Hub
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import NoOpSpan
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    parse_url,
)

if TYPE_CHECKING:
    from typing import Any

try:
    from requests.adapters import HTTPAdapter
    from requests.models import PreparedRequest, Response
    from requests.sessions import Session
except ImportError:
    raise DidNotEnable("requests is not installed")

__all__ = ["RequestsIntegration"]


class RequestsIntegration(Integration):
    identifier = "requests"

    @staticmethod
    def setup_once():
        # type: () -> None
        patch_http_adapter()
        patch_session()


def patch_session():
    # type: () -> None
    old_send = Session.send

    def _sentry_send(self, request, **kwargs):
        # type: (Session, PreparedRequest, **Any) -> Response
        hub = Hub.current

        if hub.get_integration(RequestsIntegration) is None:
            return old_send(self, request, **kwargs)

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(str(request.url), sanitize=False)

        with hub.start_span(
            op=OP.HTTP_CLIENT,
            description="%s %s"
            % (
                request.method,
                parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
            ),
        ) as span:
            span.set_data(SPANDATA.HTTP_METHOD, request.method)
            if parsed_url is not None:
                span.set_data("url", parsed_url.url)
                span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

            rv = old_send(self, request, **kwargs)

            span.set_http_status(rv.status_code)
            span.set_data("reason", rv.reason)

            return rv

    Session.send = _sentry_send


def patch_http_adapter():
    # type: () -> None
    old_send = HTTPAdapter.send

    def _sentry_send(self, *args, **kwargs):
        # type: (HTTPAdapter, *Any, **Any) -> Response
        hub = Hub.current

        if hub.get_integration(RequestsIntegration) is None:
            return old_send(self, *args, **kwargs)

        # necessary to suppress the span from low-level integration
        with NoOpSpan():
            return old_send(self, *args, **kwargs)

    HTTPAdapter.send = _sentry_send

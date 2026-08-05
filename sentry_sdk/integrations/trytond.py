import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.utils import (
    ensure_integration_enabled,
    event_from_exception,
    parse_version,
)

try:
    from trytond import __version__ as TRYTOND_VERSION  # type: ignore
    from trytond.exceptions import TrytonException  # type: ignore
    from trytond.wsgi import app  # type: ignore
except ImportError:
    raise DidNotEnable("Trytond is not installed.")

# TODO: trytond-worker, trytond-cron and trytond-admin integrations


class TrytondWSGIIntegration(Integration):
    identifier = "trytond_wsgi"
    origin = f"auto.http.{identifier}"

    def __init__(self) -> None:
        pass

    @staticmethod
    def setup_once() -> None:
        version = parse_version(TRYTOND_VERSION)
        _check_minimum_version(TrytondWSGIIntegration, version, "trytond")

        app.wsgi_app = SentryWsgiMiddleware(
            app.wsgi_app,
            span_origin=TrytondWSGIIntegration.origin,
        )

        @ensure_integration_enabled(TrytondWSGIIntegration)
        def error_handler(e: Exception) -> None:
            if isinstance(e, TrytonException):
                return
            else:
                client = sentry_sdk.get_client()
                event, hint = event_from_exception(
                    e,
                    client_options=client.options,
                    mechanism={"type": "trytond", "handled": False},
                )
                sentry_sdk.capture_event(event, hint=hint)

        @app.error_handler
        def _(app, request, e):  # type: ignore
            error_handler(e)

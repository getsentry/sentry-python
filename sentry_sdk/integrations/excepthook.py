import sys

from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration


class ExcepthookIntegration(Integration):
    identifier = "excepthook"

    @staticmethod
    def setup_once():
        sys.excepthook = _make_excepthook(sys.excepthook)


def _make_excepthook(old_excepthook):
    def sentry_sdk_excepthook(exctype, value, traceback):
        hub = Hub.current
        integration = hub.get_integration(ExcepthookIntegration)

        if integration is not None and _should_send():
            with capture_internal_exceptions():
                event, hint = event_from_exception(
                    (exctype, value, traceback),
                    client_options=hub.client.options,
                    mechanism={"type": "excepthook", "handled": False},
                )
                hub.capture_event(event, hint=hint)

        return old_excepthook(exctype, value, traceback)

    return sentry_sdk_excepthook


def _should_send():
    if hasattr(sys, "ps1"):
        # Disable the excepthook for interactive Python shells, otherwise
        # every typo gets sent to Sentry.
        return False

    return True

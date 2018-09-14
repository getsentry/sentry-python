import sys

from sentry_sdk import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration


class ExcepthookIntegration(Integration):
    identifier = "excepthook"

    def install(self):
        if hasattr(sys, "ps1"):
            # Disable the excepthook for interactive Python shells, otherwise
            # every typo gets sent to Sentry.
            return

        sys.excepthook = _make_excepthook(sys.excepthook)


def _make_excepthook(old_excepthook):
    def sentry_sdk_excepthook(exctype, value, traceback):
        with capture_internal_exceptions():
            hub = Hub.current
            event, hint = event_from_exception(
                (exctype, value, traceback),
                with_locals=hub.client.options["with_locals"],
                mechanism={"type": "excepthook", "handled": False},
            )
            hub.capture_event(event, hint=hint)

        return old_excepthook(exctype, value, traceback)

    return sentry_sdk_excepthook

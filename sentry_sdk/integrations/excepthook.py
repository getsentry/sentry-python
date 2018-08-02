import sys

from sentry_sdk import capture_exception
from sentry_sdk.hub import _internal_exceptions

from . import Integration


class ExcepthookIntegration(Integration):
    identifier = "excepthook"

    def __init__(self):
        pass

    def install(self, client):
        sys.excepthook = _make_excepthook(sys.excepthook)


def _make_excepthook(old_excepthook):
    def sentry_sdk_excepthook(exctype, value, traceback):
        with _internal_exceptions():
            capture_exception((exctype, value, traceback))

        return old_excepthook(exctype, value, traceback)

    return sentry_sdk_excepthook

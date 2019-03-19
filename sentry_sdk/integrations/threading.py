from __future__ import absolute_import

import sys

from threading import Thread

from sentry_sdk import Hub
from sentry_sdk._compat import reraise
from sentry_sdk.utils import event_from_exception
from sentry_sdk.integrations import Integration


class ThreadingIntegration(Integration):
    identifier = "threading"

    def __init__(self, propagate_hub=False):
        self.propagate_hub = propagate_hub

    @staticmethod
    def setup_once():
        old_start = Thread.start

        def sentry_start(self, *a, **kw):
            hub = Hub.current
            integration = hub.get_integration(ThreadingIntegration)
            if integration is not None:
                if integration.propagate_hub:
                    hub = Hub(hub)
                else:
                    hub = None

                self.run = _wrap_run(hub, self.run)

            return old_start(self, *a, **kw)

        Thread.start = sentry_start


def _wrap_run(parent_hub, old_run):
    def run(*a, **kw):
        hub = parent_hub or Hub.current

        with hub:
            try:
                return old_run(*a, **kw)
            except Exception:
                reraise(*_capture_exception())

    return run


def _capture_exception():
    hub = Hub.current
    exc_info = sys.exc_info()

    if hub.get_integration(ThreadingIntegration) is not None:
        event, hint = event_from_exception(
            exc_info,
            client_options=hub.client.options,
            mechanism={"type": "threading", "handled": False},
        )
        hub.capture_event(event, hint=hint)

    return exc_info

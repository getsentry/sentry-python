from __future__ import absolute_import

import sys

from threading import Thread
from types import MethodType

from sentry_sdk import Hub
from sentry_sdk._compat import reraise
from sentry_sdk.utils import event_from_exception
from sentry_sdk.integrations import Integration

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any


class RunDescriptor:
    def __init__(self, func, parent_hub):
        self.class_func = func
        self.parent_hub = parent_hub

    def __get__(self, instance, owner):
        """The descriptor which is used to patch instance method is sort of tricky and
        difficult to understand. But according to the `Python Data Model`,
        it is a proper way to prevent reference cycle using this way::

            # reference cycle between self.__dict__ and self.run.__self__
            self.run = new_run(self.run)

        Using descriptor will patch instance method with holding this instance
        inside closure rather than storing reference of itself into the attribute of instance.
        """
        if instance is None:
            return self

        def run(*a, **kw):
            hub = self.parent_hub or Hub.current
            with hub:
                try:
                    return MethodType(self.class_func, instance)(*a, **kw)
                except Exception:
                    reraise(*_capture_exception())

        return run


class ThreadingIntegration(Integration):
    identifier = "threading"

    def __init__(self, propagate_hub=False):
        self.propagate_hub = propagate_hub

    @staticmethod
    def setup_once():
        # type: () -> None
        old_start = Thread.start
        old_run = Thread.run

        def sentry_start(self, *a, **kw):
            hub = Hub.current
            integration = hub.get_integration(ThreadingIntegration)
            if integration is not None:
                if not integration.propagate_hub:
                    hub_ = None
                else:
                    hub_ = Hub(hub)

                self.__class__.run = RunDescriptor(old_run, hub_)

            return old_start(self, *a, **kw)  # type: ignore

        Thread.start = sentry_start  # type: ignore


def _capture_exception():
    hub = Hub.current
    exc_info = sys.exc_info()

    if hub.get_integration(ThreadingIntegration) is not None:
        # If an integration is there, a client has to be there.
        client = hub.client  # type: Any

        event, hint = event_from_exception(
            exc_info,
            client_options=client.options,
            mechanism={"type": "threading", "handled": False},
        )
        hub.capture_event(event, hint=hint)

    return exc_info

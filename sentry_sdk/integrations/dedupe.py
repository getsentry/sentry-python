import os

from sentry_sdk.hub import Hub
from sentry_sdk.utils import ContextVar
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional

    from sentry_sdk._types import Event, Hint


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        # type: () -> None
        self._last_seen = ContextVar("last-seen")

    @staticmethod
    def setup_once():
        # type: () -> None
        @add_global_event_processor
        def processor(event, hint):
            # type: (Event, Optional[Hint]) -> Optional[Event]
            pid = os.getpid()
            if hint is None:
                return event

            integration = Hub.current.get_integration(DedupeIntegration)

            if integration is None:
                return event

            exc_info = hint.get("exc_info", None)
            if exc_info is None:
                return event

            exc = exc_info[1]

            last_seen_pid, last_seen_exc = integration._last_seen.get((None, None))
            if last_seen_exc is exc and last_seen_pid == pid:
                return None
            integration._last_seen.set((pid, exc))
            return event

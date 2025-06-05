import sentry_sdk
from sentry_sdk.utils import ContextVar
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional

    from sentry_sdk._types import Event, Hint


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        # type: () -> None
        self._last_seen = ContextVar("last-seen")

    @staticmethod
    def _get_event_hash(event):
        # type: (Event) -> int
        """
        Create a memory-efficient hash for an event.

        Instead of storing the entire exception object, we store just enough
        information to identify it uniquely. This avoids keeping the traceback
        and local variables in memory.
        """
        event_hash = hash(
            (
                event["exception"]["values"][0]["type"],
                event["exception"]["values"][0]["value"],
                event["exception"]["values"][0]["stacktrace"]["frames"][-1]["filename"],
                event["exception"]["values"][0]["stacktrace"]["frames"][-1]["function"],
                event["exception"]["values"][0]["stacktrace"]["frames"][-1]["lineno"],
            )
        )

        return event_hash

    @staticmethod
    def setup_once():
        # type: () -> None
        @add_global_event_processor
        def processor(event, hint):
            # type: (Event, Optional[Hint]) -> Optional[Event]
            if hint is None:
                return event

            integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
            if integration is None:
                return event

            exc_info = hint.get("exc_info", None)
            if exc_info is None:
                return event

            event_hash = DedupeIntegration._get_event_hash(event)
            if integration._last_seen.get(None) == event_hash:
                return None

            integration._last_seen.set(event_hash)
            return event

    @staticmethod
    def reset_last_seen():
        # type: () -> None
        integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
        if integration is None:
            return

        integration._last_seen.set(None)

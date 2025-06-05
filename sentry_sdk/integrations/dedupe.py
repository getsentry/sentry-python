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
    def _get_exception_hash(exc):
        # type: (Exception) -> int
        """
        Create a memory-efficient hash for an exception.

        Instead of storing the entire exception object, we store just enough
        information to identify it uniquely. This avoids keeping the traceback
        and local variables in memory.
        """
        # Get the exception type name and message
        exc_type = type(exc).__name__
        exc_message = str(exc)

        # Get the first frame of the traceback if it exists
        if hasattr(exc, "__traceback__") and exc.__traceback__:
            frame = exc.__traceback__.tb_frame
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            func_name = frame.f_code.co_name
            location = f"{filename}:{lineno}:{func_name}"  # noqa: E231
        else:
            location = None

        # Create a tuple of the essential information and hash it
        return hash((exc_type, exc_message, location))

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

            exc = exc_info[1]
            exc_hash = DedupeIntegration._get_exception_hash(exc)

            if integration._last_seen.get(None) == exc_hash:
                return None

            integration._last_seen.set(exc_hash)
            return event

    @staticmethod
    def reset_last_seen():
        # type: () -> None
        integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
        if integration is None:
            return

        integration._last_seen.set(None)

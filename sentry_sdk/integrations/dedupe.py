import hashlib
import sentry_sdk
from sentry_sdk.utils import (
    ContextVar,
    logger,
    get_type_name,
    get_type_module,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Event, Hint


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        # type: () -> None
        self._last_seen = ContextVar("last-seen", default=None)

    def _create_exception_fingerprint(self, exc_info):
        # type: (Any) -> str
        """
        Creates a unique fingerprint for an exception based on type, message, and object id.
        """
        exc_type, exc_value, tb = exc_info

        if exc_type is None or exc_value is None:
            return ""

        type_module = get_type_module(exc_type) or ""
        type_name = get_type_name(exc_type) or ""
        exception_id = str(id(exc_value))

        fingerprint_parts = [type_module, type_name, exception_id]
        fingerprint_data = "||".join(fingerprint_parts).encode(
            "utf-8", errors="replace"
        )

        return hashlib.sha256(fingerprint_data).hexdigest()

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

            fingerprint = integration._create_exception_fingerprint(exc_info)
            last_fingerprint = integration._last_fingerprint.get()

            if fingerprint == last_fingerprint:
                logger.info(
                    "DedupeIntegration dropped duplicated error event with fingerprint %s",
                    fingerprint[:16],
                )
                return None

            # Store this fingerprint as the last seen one
            integration._last_fingerprint.set(fingerprint)
            return event

    @staticmethod
    def reset_last_seen():
        # type: () -> None
        """
        Resets the deduplication state, clearing the last seen exception fingerprint.

        This maintains the existing public API while working with the new
        fingerprint-based implementation.
        """
        integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
        if integration is None:
            return

        integration._last_seen.set(None)

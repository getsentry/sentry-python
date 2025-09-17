import hashlib
import sentry_sdk
from sentry_sdk.utils import (
    ContextVar,
    logger,
    get_type_name,
    get_type_module,
    get_error_message,
    iter_stacks,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Event, Hint


def _create_exception_fingerprint(exc_info):
    # type: (Any) -> str
    """
    Creates a unique fingerprint for an exception based on type, message, and traceback.

    This replaces object identity comparison to prevent memory leaks while maintaining
    accurate deduplication for the same exception (same type+message+traceback).

    Memory usage: 64 bytes (SHA256 hex string) for the last seen exception fingerprint.
    """
    exc_type, exc_value, tb = exc_info

    if exc_type is None or exc_value is None:
        return ""

    # Get exception type information
    type_module = get_type_module(exc_type) or ""
    type_name = get_type_name(exc_type) or ""

    # Get exception message
    message = get_error_message(exc_value)

    # Create traceback fingerprint from top frames (limit to avoid excessive memory usage)
    tb_parts = []
    frame_count = 0
    max_frames = 10  # Limit frames to keep memory usage low

    for tb_frame in iter_stacks(tb):
        if frame_count >= max_frames:
            break

        # Extract key frame information for fingerprint
        filename = tb_frame.tb_frame.f_code.co_filename or ""
        function_name = tb_frame.tb_frame.f_code.co_name or ""
        line_number = str(tb_frame.tb_lineno)

        # Create a compact frame fingerprint
        frame_fingerprint = "{}:{}:{}".format(
            (
                filename.split("/")[-1] if "/" in filename else filename
            ),  # Just filename, not full path
            function_name,
            line_number,
        )
        tb_parts.append(frame_fingerprint)
        frame_count += 1

    # Combine all parts for the complete fingerprint
    fingerprint_parts = [type_module, type_name, message, "|".join(tb_parts)]

    # Create SHA256 hash of the combined fingerprint
    fingerprint_data = "||".join(fingerprint_parts).encode("utf-8", errors="replace")
    return hashlib.sha256(fingerprint_data).hexdigest()


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        # type: () -> None
        # Store fingerprint of the last seen exception instead of the exception object
        # This prevents memory leaks by not holding references to exception objects
        self._last_fingerprint = ContextVar("last-fingerprint", default=None)

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

            # Create fingerprint from exception instead of storing the object
            fingerprint = _create_exception_fingerprint(exc_info)
            if not fingerprint:
                return event

            # Check if this fingerprint matches the last seen one
            last_fingerprint = integration._last_fingerprint.get()
            if last_fingerprint == fingerprint:
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

        integration._last_fingerprint.set(None)

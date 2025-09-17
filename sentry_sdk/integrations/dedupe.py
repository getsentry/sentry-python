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
from sentry_sdk.tracing_utils import _should_be_included
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Event, Hint


def _is_frame_in_app(tb_frame):
    # type: (Any) -> bool
    client = sentry_sdk.get_client()
    if not client.is_active():
        return True

    in_app_include = client.options.get("in_app_include")
    in_app_exclude = client.options.get("in_app_exclude")
    project_root = client.options.get("project_root")

    abs_path = tb_frame.tb_frame.f_code.co_filename
    namespace = tb_frame.tb_frame.f_globals.get("__name__")

    return _should_be_included(
        is_sentry_sdk_frame=False,
        namespace=namespace,
        in_app_include=in_app_include,
        in_app_exclude=in_app_exclude,
        abs_path=abs_path,
        project_root=project_root,
    )


def _create_exception_fingerprint(exc_info):
    # type: (Any) -> str
    """
    Creates a unique fingerprint for an exception based on type, message, and in-app traceback.
    """
    exc_type, exc_value, tb = exc_info

    if exc_type is None or exc_value is None:
        return ""

    type_module = get_type_module(exc_type) or ""
    type_name = get_type_name(exc_type) or ""
    message = get_error_message(exc_value)

    tb_parts = []
    frame_count = 0

    for tb_frame in iter_stacks(tb):
        if not _is_frame_in_app(tb_frame):
            continue

        file_path = tb_frame.tb_frame.f_code.co_filename or ""
        file_name = file_path.split("/")[-1] if "/" in file_path else file_path
        function_name = tb_frame.tb_frame.f_code.co_name or ""
        line_number = str(tb_frame.tb_lineno)
        frame_fingerprint = "{}:{}:{}".format(
            file_name,
            function_name,
            line_number,
        )
        tb_parts.append(frame_fingerprint)
        frame_count += 1

    fingerprint_parts = [type_module, type_name, message, "|".join(tb_parts)]
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

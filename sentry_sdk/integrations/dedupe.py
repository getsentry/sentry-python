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
from sentry_sdk.tracing_utils import _should_be_included, _get_frame_module_abs_path
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, List

    from sentry_sdk._types import Event, Hint


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        # type: () -> None
        self._last_seen = ContextVar("last-seen", default=None)
        self.in_app_include = []  # type: List[str]
        self.in_app_exclude = []  # type: List[str]
        self.project_root = None  # type: Optional[str]

    def _is_frame_in_app(self, namespace, abs_path):
        # type: (Any, Optional[str], Optional[str]) -> bool
        return _should_be_included(
            is_sentry_sdk_frame=False,
            namespace=namespace,
            in_app_include=self.in_app_include,
            in_app_exclude=self.in_app_exclude,
            abs_path=abs_path,
            project_root=self.project_root,
        )

    def _create_exception_fingerprint(self, exc_info):
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
            abs_path = _get_frame_module_abs_path(tb_frame.tb_frame) or ""
            namespace = tb_frame.tb_frame.f_globals.get("__name__")

            if not self._is_frame_in_app(namespace, abs_path):
                continue

            file_name = abs_path.split("/")[-1] if "/" in abs_path else abs_path
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
        fingerprint_data = "||".join(fingerprint_parts).encode(
            "utf-8", errors="replace"
        )

        return hashlib.sha256(fingerprint_data).hexdigest()

    @staticmethod
    def setup_once():
        # type: () -> None
        client = sentry_sdk.get_client()
        integration = client.get_integration(DedupeIntegration)
        if integration is not None:
            integration.in_app_include = client.options.get("in_app_include") or []
            integration.in_app_exclude = client.options.get("in_app_exclude") or []
            integration.project_root = client.options.get("project_root") or None

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
            last_fingerprint = integration._last_seen.get()

            if fingerprint == last_fingerprint:
                logger.info(
                    "DedupeIntegration dropped duplicated error event %s (fingerprint)",
                    fingerprint[:16],
                )
                return None

            integration._last_seen.set(fingerprint)
            return event

    @staticmethod
    def reset_last_seen():
        # type: () -> None
        """
        Resets the deduplication state, clearing the last seen exception fingerprint.
        """
        integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
        if integration is None:
            return

        integration._last_seen.set(None)

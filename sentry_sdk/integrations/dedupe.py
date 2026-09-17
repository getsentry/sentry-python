import time
from contextvars import ContextVar
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.utils import logger

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Event, Hint


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self) -> None:
        self._last_seen: "ContextVar[Any]" = ContextVar("last-seen")

    @staticmethod
    def setup_once() -> None:
        @add_global_event_processor
        def processor(event: "Event", hint: "Optional[Hint]") -> "Optional[Event]":
            if hint is None:
                return event

            integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
            if integration is None:
                return event

            exc_info = hint.get("exc_info", None)
            if exc_info is None:
                return event

            last_seen_entries = integration._last_seen.get(None)
            updated_cache_entries = set()

            exc = exc_info[1]
            now = time.time()

            if not last_seen_entries:
                integration._last_seen.set([(exc, now)])
                return event

            found_duplicate = False
            for cache_item in last_seen_entries:
                exception_item, last_seen = cache_item
                if last_seen < (now - 60):  # 1 minute TTL
                    continue

                if exc is exception_item:
                    updated_cache_entries.add((exception_item, now))
                    found_duplicate = True
                    continue

                updated_cache_entries.add((exception_item, last_seen))

            if not found_duplicate:
                updated_cache_entries.add((exc, now))

            integration._last_seen.set(updated_cache_entries)

            if found_duplicate:
                logger.info("DedupeIntegration dropped duplicated error event %s", exc)
                return None
            else:
                return event

    @staticmethod
    def reset_last_seen() -> None:
        integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
        if integration is None:
            return

        integration._last_seen.set(None)

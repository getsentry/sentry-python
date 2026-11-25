from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_has_unhandled_exception,
    logger,
)

if TYPE_CHECKING:
    from typing import Optional

    from sentry_sdk._types import Event, Hint


class DedupeIntegration(Integration):
    identifier = "dedupe"

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

            exc = exc_info[1]
            is_unhandled = event_has_unhandled_exception(event)
            seen_state = getattr(exc, "_handled_by_sentry", None)

            if seen_state and (not is_unhandled or seen_state == "unhandled"):
                logger.info("DedupeIntegration dropped duplicated error event %s", exc)
                return None

            with capture_internal_exceptions():
                exc._handled_by_sentry = "unhandled" if is_unhandled else "handled"
            return event

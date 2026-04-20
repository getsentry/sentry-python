import weakref

import sentry_sdk
from sentry_sdk.utils import ContextVar, logger
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Tuple, Type, Any

    from sentry_sdk._types import Event, Hint


def _safe_args_hash(args: "Tuple[Any, ...]") -> int:
    try:
        return hash(args)
    except Exception:
        try:
            return hash(repr(args))
        except Exception:
            return 0


def _fingerprint(exc: BaseException) -> "Tuple[Type[BaseException], int]":
    return (type(exc), _safe_args_hash(exc.args))


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self) -> None:
        self._last_seen = ContextVar("last-seen")

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

            last_seen = integration._last_seen.get(None)
            exc = exc_info[1]

            is_duplicate = False
            if isinstance(last_seen, weakref.ref):
                is_duplicate = last_seen() is exc
            elif isinstance(last_seen, tuple):
                is_duplicate = last_seen == _fingerprint(exc)

            if is_duplicate:
                logger.info("DedupeIntegration dropped duplicated error event %s", exc)
                return None

            # we can only weakref non builtin types
            try:
                integration._last_seen.set(weakref.ref(exc))
            except TypeError:
                integration._last_seen.set(_fingerprint(exc))

            return event

    @staticmethod
    def reset_last_seen() -> None:
        integration = sentry_sdk.get_client().get_integration(DedupeIntegration)
        if integration is None:
            return

        integration._last_seen.set(None)

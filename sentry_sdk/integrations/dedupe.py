import weakref

from sentry_sdk.hub import _internal_exceptions
from sentry_sdk.api import get_current_hub, configure_scope
from sentry_sdk.utils import ContextVar

from . import Integration


last_seen = ContextVar("last-seen")


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        self._exceptions_seen = weakref.WeakKeyDictionary()

    def install(self):
        with configure_scope() as scope:

            @scope.add_error_processor
            def processor(event, error):
                exc_info = event.get("__sentry_exc_info")
                if exc_info and exc_info[1] is not None:
                    exc = exc_info[1]
                    if last_seen.get(None) is exc:
                        seen = True
                    else:
                        seen = False
                        last_seen.set(exc)
                    if seen:
                        return None
                return event

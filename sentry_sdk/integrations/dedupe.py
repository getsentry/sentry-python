import weakref

from sentry_sdk.hub import _internal_exceptions
from sentry_sdk.api import get_current_hub, configure_scope

from . import Integration


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        self.exceptions_seen = weakref.WeakKeyDictionary()

    def install(self):
        with configure_scope() as scope:

            @scope.add_error_processor
            def processor(event, error):
                exc_info = event.get("__sentry_exc_info")
                if exc_info and exc_info[1] is not None:
                    try:
                        seen = self._exceptions_seen.get(exc_info[1]) is not None
                        self._exceptions_seen[exc_info[1]] = True
                    except Exception:
                        seen = False
                    if seen:
                        return None
                return event

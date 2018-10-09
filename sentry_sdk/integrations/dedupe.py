from sentry_sdk.hub import Hub
from sentry_sdk.utils import ContextVar
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor


class DedupeIntegration(Integration):
    identifier = "dedupe"

    def __init__(self):
        self._last_seen = ContextVar("last-seen")

    @staticmethod
    def setup_once():
        @add_global_event_processor
        def processor(event, hint):
            integration = Hub.current.get_integration(DedupeIntegration)
            if integration is not None:
                exc_info = hint.get("exc_info", None)
                if exc_info is not None:
                    exc = exc_info[1]
                    if integration._last_seen.get(None) is exc:
                        return
                    integration._last_seen.set(exc)
            return event

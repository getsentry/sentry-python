import pytest
import sentry_sdk


@pytest.fixture
def capture_exceptions(monkeypatch):
    def inner():
        errors = set()
        old_capture_event_hub = sentry_sdk.Hub.capture_event
        old_capture_event_scope = sentry_sdk.Scope.capture_event

        def capture_event_hub(self, event, hint=None, scope=None):
            if hint:
                if "exc_info" in hint:
                    error = hint["exc_info"][1]
                    errors.add(error)
            return old_capture_event_hub(self, event, hint=hint, scope=scope)

        def capture_event_scope(self, event, hint=None, scope=None):
            if hint:
                if "exc_info" in hint:
                    error = hint["exc_info"][1]
                    errors.add(error)
            return old_capture_event_scope(self, event, hint=hint, scope=scope)

        monkeypatch.setattr(sentry_sdk.Hub, "capture_event", capture_event_hub)
        monkeypatch.setattr(sentry_sdk.Scope, "capture_event", capture_event_scope)

        return errors

    return inner


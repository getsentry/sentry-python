import pytest
import sentry_sdk


@pytest.fixture
def capture_exceptions(monkeypatch):
    def inner():
        errors = set()
        old_capture_event = sentry_sdk.Hub.capture_event

        def capture_event(self, event, hint=None):
            if hint:
                if "exc_info" in hint:
                    error = hint["exc_info"][1]
                    errors.add(error)
            return old_capture_event(self, event, hint=hint)

        monkeypatch.setattr(sentry_sdk.Hub, "capture_event", capture_event)
        return errors

    return inner

import sys
import pytest
import sentry_sdk


@pytest.fixture
def capture_exceptions(monkeypatch):
    def inner():
        errors = []
        old_capture_exception = sentry_sdk.Hub.current.capture_exception

        def capture_exception(error=None):
            errors.append(error or sys.exc_info()[1])
            return old_capture_exception(error)

        monkeypatch.setattr(
            sentry_sdk.Hub.current, "capture_exception", capture_exception
        )
        return errors

    return inner


@pytest.fixture
def capture_events(monkeypatch):
    def inner():
        events = []
        test_client = sentry_sdk.Hub.current.client
        old_capture_event = test_client._transport.capture_event

        def append(event):
            events.append(event)
            return old_capture_event(event)

        monkeypatch.setattr(test_client._transport, "capture_event", append)
        return events

    return inner

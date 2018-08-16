import sys
import pytest
import sentry_sdk


@pytest.fixture
def capture_exceptions(monkeypatch):
    def inner():
        errors = set()
        old_capture_exception = sentry_sdk.Hub.current.capture_exception

        def capture_exception(error=None):
            given_error = error
            error = error or sys.exc_info()[1]
            if isinstance(error, tuple):
                error = error[1]
            errors.add(error)
            return old_capture_exception(given_error)

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

import pytest
import sentry_sdk
sentry_sdk.init()


@pytest.fixture
def capture_exceptions(monkeypatch):
    errors = []
    def capture_exception(error=None):
        errors.append(error or sys.exc_info()[1])

    monkeypatch.setattr(sentry_sdk.Hub.current,
                        'capture_exception', capture_exception)
    return errors


@pytest.fixture
def capture_events(monkeypatch):
    events = []
    monkeypatch.setattr(sentry_sdk.Hub.current, 'capture_event', events.append)
    return events

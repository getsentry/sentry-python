import sys
import pytest
import sentry_sdk

from sentry_sdk.client import Transport


class TestTransport(Transport):
    def __init__(self):
        pass

    def start(self):
        pass

    def close(self):
        pass

    def capture_event(self, event):
        pass

    dsn = "LOL"


@pytest.fixture(autouse=True)
def set_test_transport(monkeypatch):
    test_client = sentry_sdk.Hub.current.client
    if test_client:
        monkeypatch.setattr(test_client, "_transport", TestTransport())


@pytest.fixture(autouse=True)
def reraise_internal_exceptions(monkeypatch):
    def capture_internal_exception(error=None):
        if not error:
            raise
        raise error

    monkeypatch.setattr(
        sentry_sdk.get_current_hub(),
        "capture_internal_exception",
        capture_internal_exception,
    )


@pytest.fixture
def capture_exceptions(monkeypatch):
    errors = []

    def capture_exception(error=None):
        errors.append(error or sys.exc_info()[1])

    monkeypatch.setattr(sentry_sdk.Hub.current, "capture_exception", capture_exception)
    return errors


@pytest.fixture
def capture_events(monkeypatch):
    events = []
    test_client = sentry_sdk.Hub.current.client
    monkeypatch.setattr(test_client._transport, "capture_event", events.append)
    return events

import sys
import pytest
import sentry_sdk

from sentry_sdk.client import Transport


@pytest.fixture
def monkeypatch_test_transport(monkeypatch):
    return lambda client: monkeypatch.setattr(client, "_transport", TestTransport())


@pytest.fixture
def sentry_init(monkeypatch_test_transport):
    def inner(*a, **kw):
        client = sentry_sdk.Client(*a, **kw)
        monkeypatch_test_transport(client)
        sentry_sdk.Hub.current.bind_client(client)

    return inner


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
    def inner():
        errors = []

        def capture_exception(error=None):
            errors.append(error or sys.exc_info()[1])

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
        monkeypatch.setattr(test_client._transport, "capture_event", events.append)
        return events

    return inner

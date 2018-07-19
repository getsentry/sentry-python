import pytest
import sentry_sdk
from sentry_sdk.client import Client, Transport

class TestClient(Client):
    def __init__(self):
        pass

    def capture_internal_exception(self, error=None):
        if not error:
            raise
        raise error

    dsn = 'LOL'
    options = {'with_locals': False, 'release': 'fake_release', 'environment': 'fake_environment', 'server_name': 'fake_servername'}
    _transport = None


class TestTransport(Transport):
    def __init__(self):
        pass

    def start(self):
        pass

    def close(self):
        pass

test_client = TestClient()

sentry_sdk.init()
sentry_sdk.get_current_hub().bind_client(test_client)


@pytest.fixture
def capture_exceptions(monkeypatch):
    errors = []
    def capture_exception(error=None):
        errors.append(error or sys.exc_info()[1])

    monkeypatch.setattr(sentry_sdk.Hub.current, 'capture_exception', capture_exception)
    return errors


@pytest.fixture
def capture_events(monkeypatch):
    events = []
    monkeypatch.setattr(test_client, '_transport', TestTransport())
    monkeypatch.setattr(test_client._transport, 'capture_event', events.append)
    return events

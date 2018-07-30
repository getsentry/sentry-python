import pytest
from sentry_sdk import Client
from sentry_sdk.utils import Event, Dsn
from sentry_sdk.transport import Transport


def test_transport_option(monkeypatch):
    dsn = "https://foo@sentry.io/123"
    dsn2 = "https://bar@sentry.io/124"
    assert str(Client(dsn=dsn).dsn) == dsn
    assert Client().dsn is None
    with pytest.raises(ValueError):
        Client(dsn, transport=Transport(Dsn(dsn2)))
    with pytest.raises(ValueError):
        Client(dsn, transport=Transport(Dsn(dsn)))
    assert str(Client(transport=Transport(Dsn(dsn2))).dsn) == dsn2

    monkeypatch.setenv("SENTRY_DSN", dsn)
    assert str(Client(transport=Transport(Dsn(dsn2))).dsn) == dsn2


def test_ignore_errors():
    def e(exc_type):
        rv = Event()
        rv._exc_value = exc_type()
        return rv

    class EventCaptured(Exception):
        pass

    class TestTransport(Transport):
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def capture_event(self, event):
            raise EventCaptured()

    c = Client(ignore_errors=[Exception], transport=TestTransport())
    c.capture_event(e(Exception))
    c.capture_event(e(ValueError))
    pytest.raises(EventCaptured, lambda: c.capture_event(e(BaseException)))

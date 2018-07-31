import time
import pytest
import sys
import subprocess
from textwrap import dedent
from sentry_sdk import Client
from sentry_sdk.transport import Transport
from sentry_sdk.utils import Event, Dsn


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


@pytest.mark.parametrize("num_messages", [10, 20])
def test_atexit(tmpdir, monkeypatch, num_messages):
    app = tmpdir.join("app.py")
    app.write(
        dedent(
            """
    import time
    from sentry_sdk import init, transport, capture_message

    def send_event(pool, event, auth):
        time.sleep(0.1)
        print(event["message"])

    transport.send_event = send_event
    init("http://foobar@localhost/123", drain_timeout={num_messages})

    for _ in range({num_messages}):
        capture_message("HI")
    """.format(
                num_messages=num_messages
            )
        )
    )

    start = time.time()
    output = subprocess.check_output([sys.executable, str(app)])
    end = time.time()
    assert int(end - start) == num_messages / 10
    assert output.count("HI") == num_messages

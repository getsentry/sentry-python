# coding: utf-8
import time
import pytest
import sys
import subprocess
from datetime import datetime
from textwrap import dedent
from sentry_sdk import Hub, Client, configure_scope, capture_message, add_breadcrumb
from sentry_sdk.hub import HubMeta
from sentry_sdk.transport import Transport
from sentry_sdk.utils import Dsn, event_from_exception


class EventCaptured(Exception):
    pass


class _TestTransport(Transport):
    def __init__(self, *a, **kw):
        pass

    def start(self):
        pass

    def capture_event(self, event):
        raise EventCaptured()


def test_transport_option(monkeypatch):
    dsn = "https://foo@sentry.io/123"
    dsn2 = "https://bar@sentry.io/124"
    assert str(Client(dsn=dsn).dsn) == dsn
    assert Client().dsn is None
    assert str(Client(transport=Transport(Dsn(dsn2))).dsn) == dsn2

    monkeypatch.setenv("SENTRY_DSN", dsn)
    assert str(Client(transport=Transport(Dsn(dsn2))).dsn) == dsn2


def test_ignore_errors():
    def e(exc_type):
        return event_from_exception(exc_type())

    class MyDivisionError(ZeroDivisionError):
        pass

    c = Client(ignore_errors=[ZeroDivisionError], transport=_TestTransport())
    c.capture_event(e(ZeroDivisionError))
    c.capture_event(e(MyDivisionError))
    pytest.raises(EventCaptured, lambda: c.capture_event(e(ValueError)))


def test_capture_event_works():
    c = Client(transport=_TestTransport())
    pytest.raises(EventCaptured, lambda: c.capture_event({}))
    pytest.raises(EventCaptured, lambda: c.capture_event({}))


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
    init("http://foobar@localhost/123", shutdown_timeout={num_messages})

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
    assert output.count(b"HI") == num_messages


def test_configure_scope_available(sentry_init, request, monkeypatch):
    # Test that scope is configured if client is configured
    sentry_init()

    with configure_scope() as scope:
        assert scope is Hub.current._stack[-1][1]
        scope.set_tag("foo", "bar")

    calls = []

    def callback(scope):
        calls.append(scope)
        scope.set_tag("foo", "bar")

    assert configure_scope(callback) is None
    assert len(calls) == 1
    assert calls[0] is Hub.current._stack[-1][1]


@pytest.mark.parametrize("no_sdk", (True, False))
def test_configure_scope_unavailable(no_sdk, monkeypatch):
    if no_sdk:
        # Emulate sentry_minimal without SDK installation: callbacks are not called
        monkeypatch.setattr(HubMeta, "current", None)
        assert not Hub.current
    else:
        # Still, no client configured
        assert Hub.current

    calls = []

    def callback(scope):
        calls.append(scope)
        scope.set_tag("foo", "bar")

    with configure_scope() as scope:
        scope.set_tag("foo", "bar")

    assert configure_scope(callback) is None
    assert not calls


def test_transport_works(sentry_init, httpserver, request, capsys):
    httpserver.serve_content("ok", 200)

    client = Client("http://foobar@{}/123".format(httpserver.url[len("http://") :]))
    Hub.current.bind_client(client)
    request.addfinalizer(lambda: Hub.current.bind_client(None))

    add_breadcrumb(level="info", message="i like bread", timestamp=datetime.now())
    capture_message("löl")
    client.drain_events()

    out, err = capsys.readouterr()
    assert not err and not out
    assert httpserver.requests

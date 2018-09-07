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
from sentry_sdk._compat import reraise, text_type


class EventCaptured(Exception):
    pass


class _TestTransport(Transport):
    def capture_event(self, event):
        raise EventCaptured()


def test_transport_option(monkeypatch):
    dsn = "https://foo@sentry.io/123"
    dsn2 = "https://bar@sentry.io/124"
    assert str(Client(dsn=dsn).dsn) == dsn
    assert Client().dsn is None

    monkeypatch.setenv("SENTRY_DSN", dsn)
    transport = Transport({"dsn": dsn2})
    assert text_type(transport.parsed_dsn) == dsn2
    assert str(Client(transport=transport).dsn) == dsn


def test_simple_transport():
    events = []
    with Hub(Client(transport=events.append)):
        capture_message("Hello World!")
    assert events[0]["message"] == "Hello World!"


def test_ignore_errors():
    class MyDivisionError(ZeroDivisionError):
        pass

    def raise_it(exc_info):
        reraise(*exc_info)

    hub = Hub(Client(ignore_errors=[ZeroDivisionError], transport=_TestTransport()))
    hub._capture_internal_exception = raise_it

    def e(exc):
        try:
            raise exc
        except Exception:
            hub.capture_exception()

    e(ZeroDivisionError())
    e(MyDivisionError())
    pytest.raises(EventCaptured, lambda: e(ValueError()))


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

    def send_event(self, event):
        time.sleep(0.1)
        print(event["message"])

    transport.HttpTransport._send_event = send_event
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
        # Emulate minimal without SDK installation: callbacks are not called
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


def test_transport_works(httpserver, request, capsys):
    httpserver.serve_content("ok", 200)

    client = Client("http://foobar@{}/123".format(httpserver.url[len("http://") :]))
    Hub.current.bind_client(client)
    request.addfinalizer(lambda: Hub.current.bind_client(None))

    add_breadcrumb(level="info", message="i like bread", timestamp=datetime.now())
    capture_message("löl")
    client.close()

    out, err = capsys.readouterr()
    assert not err and not out
    assert httpserver.requests


@pytest.mark.tests_internal_exceptions
def test_client_debug_option_enabled(sentry_init, caplog):
    sentry_init(debug=True)

    Hub.current._capture_internal_exception((ValueError, ValueError("OK"), None))
    assert "OK" in caplog.text


@pytest.mark.tests_internal_exceptions
@pytest.mark.parametrize("with_client", (True, False))
def test_client_debug_option_disabled(with_client, sentry_init, caplog):
    if with_client:
        sentry_init()

    Hub.current._capture_internal_exception((ValueError, ValueError("OK"), None))
    assert "OK" not in caplog.text

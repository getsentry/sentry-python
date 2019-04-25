# coding: utf-8
import json
import logging
import pytest
import subprocess
import sys
import time

from datetime import datetime
from textwrap import dedent
from sentry_sdk import (
    Hub,
    Client,
    configure_scope,
    capture_message,
    add_breadcrumb,
    capture_exception,
)
from sentry_sdk.hub import HubMeta
from sentry_sdk.transport import Transport
from sentry_sdk._compat import reraise, text_type, PY2
from sentry_sdk.utils import HAS_CHAINED_EXCEPTIONS

if PY2:
    # Importing ABCs from collections is deprecated, and will stop working in 3.8
    # https://github.com/python/cpython/blob/master/Lib/collections/__init__.py#L49
    from collections import Mapping
else:
    # New in 3.3
    # https://docs.python.org/3/library/collections.abc.html
    from collections.abc import Mapping


class EventCaptured(Exception):
    pass


class _TestTransport(Transport):
    def capture_event(self, event):
        raise EventCaptured(event)


def test_transport_option(monkeypatch):
    dsn = "https://foo@sentry.io/123"
    dsn2 = "https://bar@sentry.io/124"
    assert str(Client(dsn=dsn).dsn) == dsn
    assert Client().dsn is None

    monkeypatch.setenv("SENTRY_DSN", dsn)
    transport = Transport({"dsn": dsn2})
    assert text_type(transport.parsed_dsn) == dsn2
    assert str(Client(transport=transport).dsn) == dsn


def test_proxy_http_use(monkeypatch):
    client = Client("http://foo@sentry.io/123", http_proxy="http://localhost/123")
    assert client.transport._pool.proxy.scheme == "http"


def test_proxy_https_use(monkeypatch):
    client = Client("https://foo@sentry.io/123", http_proxy="https://localhost/123")
    assert client.transport._pool.proxy.scheme == "https"


def test_proxy_both_select_http(monkeypatch):
    client = Client(
        "http://foo@sentry.io/123",
        https_proxy="https://localhost/123",
        http_proxy="http://localhost/123",
    )
    assert client.transport._pool.proxy.scheme == "http"


def test_proxy_both_select_https(monkeypatch):
    client = Client(
        "https://foo@sentry.io/123",
        https_proxy="https://localhost/123",
        http_proxy="http://localhost/123",
    )
    assert client.transport._pool.proxy.scheme == "https"


def test_proxy_http_fallback_http(monkeypatch):
    client = Client("https://foo@sentry.io/123", http_proxy="http://localhost/123")
    assert client.transport._pool.proxy.scheme == "http"


def test_proxy_none_noenv(monkeypatch):
    client = Client("http://foo@sentry.io/123")
    assert client.transport._pool.proxy is None


def test_proxy_none_httpenv_select(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    client = Client("http://foo@sentry.io/123")
    assert client.transport._pool.proxy.scheme == "http"


def test_proxy_none_httpsenv_select(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://localhost/123")
    client = Client("https://foo@sentry.io/123")
    assert client.transport._pool.proxy.scheme == "https"


def test_proxy_none_httpenv_fallback(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    client = Client("https://foo@sentry.io/123")
    assert client.transport._pool.proxy.scheme == "http"


def test_proxy_bothselect_bothen(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    monkeypatch.setenv("HTTPS_PROXY", "https://localhost/123")
    client = Client("https://foo@sentry.io/123", http_proxy="", https_proxy="")
    assert client.transport._pool.proxy is None


def test_proxy_bothavoid_bothenv(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    monkeypatch.setenv("HTTPS_PROXY", "https://localhost/123")
    client = Client("https://foo@sentry.io/123", http_proxy=None, https_proxy=None)
    assert client.transport._pool.proxy.scheme == "https"


def test_proxy_bothselect_httpenv(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    client = Client("https://foo@sentry.io/123", http_proxy=None, https_proxy=None)
    assert client.transport._pool.proxy.scheme == "http"


def test_proxy_httpselect_bothenv(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    monkeypatch.setenv("HTTPS_PROXY", "https://localhost/123")
    client = Client("https://foo@sentry.io/123", http_proxy=None, https_proxy="")
    assert client.transport._pool.proxy.scheme == "http"


def test_proxy_httpsselect_bothenv(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    monkeypatch.setenv("HTTPS_PROXY", "https://localhost/123")
    client = Client("https://foo@sentry.io/123", http_proxy="", https_proxy=None)
    assert client.transport._pool.proxy.scheme == "https"


def test_proxy_httpselect_httpsenv(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://localhost/123")
    client = Client("https://foo@sentry.io/123", http_proxy=None, https_proxy="")
    assert client.transport._pool.proxy is None


def test_proxy_httpsselect_bothenv_http(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://localhost/123")
    monkeypatch.setenv("HTTPS_PROXY", "https://localhost/123")
    client = Client("http://foo@sentry.io/123", http_proxy=None, https_proxy=None)
    assert client.transport._pool.proxy.scheme == "http"


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


def test_with_locals_enabled():
    events = []
    hub = Hub(Client(with_locals=True, transport=events.append))
    try:
        1 / 0
    except Exception:
        hub.capture_exception()

    event, = events

    assert all(
        frame["vars"]
        for frame in event["exception"]["values"][0]["stacktrace"]["frames"]
    )


def test_with_locals_disabled():
    events = []
    hub = Hub(Client(with_locals=False, transport=events.append))
    try:
        1 / 0
    except Exception:
        hub.capture_exception()

    event, = events

    assert all(
        "vars" not in frame
        for frame in event["exception"]["values"][0]["stacktrace"]["frames"]
    )


def test_attach_stacktrace_enabled():
    events = []
    hub = Hub(Client(attach_stacktrace=True, transport=events.append))

    def foo():
        bar()

    def bar():
        hub.capture_message("HI")

    foo()

    event, = events
    thread, = event["threads"]["values"]
    functions = [x["function"] for x in thread["stacktrace"]["frames"]]
    assert functions[-2:] == ["foo", "bar"]


def test_attach_stacktrace_enabled_no_locals():
    events = []
    hub = Hub(
        Client(attach_stacktrace=True, with_locals=False, transport=events.append)
    )

    def foo():
        bar()

    def bar():
        hub.capture_message("HI")

    foo()

    event, = events
    thread, = event["threads"]["values"]
    local_vars = [x.get("vars") for x in thread["stacktrace"]["frames"]]
    assert local_vars[-2:] == [None, None]


def test_attach_stacktrace_in_app(sentry_init, capture_events):
    sentry_init(attach_stacktrace=True, in_app_exclude=["_pytest"])
    events = capture_events()

    capture_message("hi")

    event, = events
    thread, = event["threads"]["values"]
    frames = thread["stacktrace"]["frames"]
    pytest_frames = [f for f in frames if f["module"].startswith("_pytest")]
    assert pytest_frames
    assert all(f["in_app"] is False for f in pytest_frames)
    assert any(f["in_app"] for f in frames)


def test_attach_stacktrace_disabled():
    events = []
    hub = Hub(Client(attach_stacktrace=False, transport=events.append))
    hub.capture_message("HI")

    event, = events
    assert "threads" not in event


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

    # Each message takes at least 0.1 seconds to process
    assert int(end - start) >= num_messages / 10

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


@pytest.mark.parametrize("debug", (True, False))
def test_transport_works(httpserver, request, capsys, caplog, debug):
    httpserver.serve_content("ok", 200)
    caplog.set_level(logging.DEBUG)

    client = Client(
        "http://foobar@{}/123".format(httpserver.url[len("http://") :]), debug=debug
    )
    Hub.current.bind_client(client)
    request.addfinalizer(lambda: Hub.current.bind_client(None))

    add_breadcrumb(level="info", message="i like bread", timestamp=datetime.now())
    capture_message("löl")
    client.close()

    out, err = capsys.readouterr()
    assert not err and not out
    assert httpserver.requests

    assert any("Sending info event" in record.msg for record in caplog.records) == debug


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


def test_scope_initialized_before_client(sentry_init, capture_events):
    """
    This is a consequence of how configure_scope() works. We must
    make `configure_scope()` a noop if no client is configured. Even
    if the user later configures a client: We don't know that.
    """
    with configure_scope() as scope:
        scope.set_tag("foo", 42)

    sentry_init()

    events = capture_events()
    capture_message("hi")
    event, = events

    assert "tags" not in event


def test_weird_chars(sentry_init, capture_events):
    sentry_init()
    events = capture_events()
    capture_message(u"föö".encode("latin1"))
    event, = events
    assert json.loads(json.dumps(event)) == event


def test_nan(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        nan = float("nan")  # noqa
        1 / 0
    except Exception:
        capture_exception()

    event, = events
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    frame, = frames
    assert frame["vars"]["nan"] == "nan"


def test_cyclic_frame_vars(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        a = {}
        a["a"] = a
        1 / 0
    except Exception:
        capture_exception()

    event, = events
    assert event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["a"] == {
        "a": "<cyclic>"
    }


def test_cyclic_data(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    with configure_scope() as scope:
        data = {}
        data["is_cyclic"] = data

        other_data = ""
        data["not_cyclic"] = other_data
        data["not_cyclic2"] = other_data
        scope.set_extra("foo", data)

    capture_message("hi")
    event, = events

    data = event["extra"]["foo"]
    assert data == {"not_cyclic2": "''", "not_cyclic": "''", "is_cyclic": "<cyclic>"}


def test_databag_stripping(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        a = "A" * 16000  # noqa
        1 / 0
    except Exception:
        capture_exception()

    event, = events

    assert len(json.dumps(event)) < 10000


def test_databag_breadth_stripping(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        a = ["a"] * 16000  # noqa
        1 / 0
    except Exception:
        capture_exception()

    event, = events

    assert len(json.dumps(event)) < 10000


@pytest.mark.skipif(not HAS_CHAINED_EXCEPTIONS, reason="Only works on 3.3+")
def test_chained_exceptions(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        try:
            raise ValueError()
        except Exception:
            1 / 0
    except Exception:
        capture_exception()

    event, = events

    e1, e2 = event["exception"]["values"]

    # This is the order all other SDKs send chained exceptions in. Including
    # Raven-Python.

    assert e1["type"] == "ValueError"
    assert e2["type"] == "ZeroDivisionError"


@pytest.mark.tests_internal_exceptions
def test_broken_mapping(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    class C(Mapping):
        def broken(self, *args, **kwargs):
            raise Exception("broken")

        __getitem__ = broken
        __setitem__ = broken
        __delitem__ = broken
        __iter__ = broken
        __len__ = broken

        def __repr__(self):
            return "broken"

    try:
        a = C()  # noqa
        1 / 0
    except Exception:
        capture_exception()

    event, = events
    assert (
        event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["a"]
        == "<broken repr>"
    )


def test_errno_errors(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    class Foo(Exception):
        errno = 69

    capture_exception(Foo())

    event, = events

    exception, = event["exception"]["values"]
    assert exception["mechanism"]["meta"]["errno"]["number"] == 69

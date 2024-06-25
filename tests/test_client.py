import os
import json
import subprocess
import sys
import time
from collections.abc import Mapping
from textwrap import dedent
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import (
    Hub,
    Client,
    add_breadcrumb,
    configure_scope,
    capture_message,
    capture_exception,
    capture_event,
    set_tag,
)
from sentry_sdk.integrations.executing import ExecutingIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.serializer import MAX_DATABAG_BREADTH
from sentry_sdk.consts import DEFAULT_MAX_BREADCRUMBS, DEFAULT_MAX_VALUE_LENGTH
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional, Union
    from sentry_sdk._types import Event


class EnvelopeCapturedError(Exception):
    pass


class _TestTransport(Transport):
    def capture_envelope(self, envelope):
        raise EnvelopeCapturedError(envelope)


def test_transport_option(monkeypatch):
    if "SENTRY_DSN" in os.environ:
        monkeypatch.delenv("SENTRY_DSN")

    dsn = "https://foo@sentry.io/123"
    dsn2 = "https://bar@sentry.io/124"
    assert str(Client(dsn=dsn).dsn) == dsn
    assert Client().dsn is None

    monkeypatch.setenv("SENTRY_DSN", dsn)
    transport = _TestTransport({"dsn": dsn2})
    assert str(transport.parsed_dsn) == dsn2
    assert str(Client(transport=transport).dsn) == dsn


@pytest.mark.parametrize(
    "testcase",
    [
        {
            "dsn": "http://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "arg_http_proxy": "http://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "arg_http_proxy": "https://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_scheme": "https",
        },
        {
            "dsn": "http://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "arg_http_proxy": "http://localhost/123",
            "arg_https_proxy": "https://localhost/123",
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "arg_http_proxy": "http://localhost/123",
            "arg_https_proxy": "https://localhost/123",
            "expected_proxy_scheme": "https",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "arg_http_proxy": "http://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "http://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": None,
        },
        {
            "dsn": "http://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": None,
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": "https://localhost/123",
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": "https",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": None,
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": "https://localhost/123",
            "arg_http_proxy": "",
            "arg_https_proxy": "",
            "expected_proxy_scheme": None,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": "https://localhost/123",
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": "https",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": None,
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": "https://localhost/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "",
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": "https://localhost/123",
            "arg_http_proxy": "",
            "arg_https_proxy": None,
            "expected_proxy_scheme": "https",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": "https://localhost/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "",
            "expected_proxy_scheme": None,
        },
        {
            "dsn": "http://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": "https://localhost/123",
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": "http",
        },
        # NO_PROXY testcases
        {
            "dsn": "http://foo@sentry.io/123",
            "env_http_proxy": "http://localhost/123",
            "env_https_proxy": None,
            "env_no_proxy": "sentry.io,example.com",
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": None,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": "https://localhost/123",
            "env_no_proxy": "example.com,sentry.io",
            "arg_http_proxy": None,
            "arg_https_proxy": None,
            "expected_proxy_scheme": None,
        },
        {
            "dsn": "http://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "env_no_proxy": "sentry.io,example.com",
            "arg_http_proxy": "http://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_scheme": "http",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "env_no_proxy": "sentry.io,example.com",
            "arg_http_proxy": None,
            "arg_https_proxy": "https://localhost/123",
            "expected_proxy_scheme": "https",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "env_http_proxy": None,
            "env_https_proxy": None,
            "env_no_proxy": "sentry.io,example.com",
            "arg_http_proxy": None,
            "arg_https_proxy": "https://localhost/123",
            "expected_proxy_scheme": "https",
            "arg_proxy_headers": {"Test-Header": "foo-bar"},
        },
    ],
)
def test_proxy(monkeypatch, testcase):
    if testcase["env_http_proxy"] is not None:
        monkeypatch.setenv("HTTP_PROXY", testcase["env_http_proxy"])
    if testcase["env_https_proxy"] is not None:
        monkeypatch.setenv("HTTPS_PROXY", testcase["env_https_proxy"])
    if testcase.get("env_no_proxy") is not None:
        monkeypatch.setenv("NO_PROXY", testcase["env_no_proxy"])

    kwargs = {}

    if testcase["arg_http_proxy"] is not None:
        kwargs["http_proxy"] = testcase["arg_http_proxy"]
    if testcase["arg_https_proxy"] is not None:
        kwargs["https_proxy"] = testcase["arg_https_proxy"]
    if testcase.get("arg_proxy_headers") is not None:
        kwargs["proxy_headers"] = testcase["arg_proxy_headers"]

    client = Client(testcase["dsn"], **kwargs)

    if testcase["expected_proxy_scheme"] is None:
        assert client.transport._pool.proxy is None
    else:
        assert client.transport._pool.proxy.scheme == testcase["expected_proxy_scheme"]

        if testcase.get("arg_proxy_headers") is not None:
            assert client.transport._pool.proxy_headers == testcase["arg_proxy_headers"]


@pytest.mark.parametrize(
    "testcase",
    [
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "http://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_class": "<class 'urllib3.poolmanager.ProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks4a://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks4://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks5h://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks5://localhost/123",
            "arg_https_proxy": None,
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks4a://localhost/123",
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks4://localhost/123",
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks5h://localhost/123",
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks5://localhost/123",
            "expected_proxy_class": "<class 'urllib3.contrib.socks.SOCKSProxyManager'>",
        },
    ],
)
def test_socks_proxy(testcase):
    kwargs = {}

    if testcase["arg_http_proxy"] is not None:
        kwargs["http_proxy"] = testcase["arg_http_proxy"]
    if testcase["arg_https_proxy"] is not None:
        kwargs["https_proxy"] = testcase["arg_https_proxy"]

    client = Client(testcase["dsn"], **kwargs)
    assert str(type(client.transport._pool)) == testcase["expected_proxy_class"]


def test_simple_transport(sentry_init):
    events = []
    sentry_init(transport=events.append)
    capture_message("Hello World!")
    assert events[0]["message"] == "Hello World!"


def test_ignore_errors(sentry_init, capture_events):
    with mock.patch(
        "sentry_sdk.scope.Scope._capture_internal_exception"
    ) as mock_capture_internal_exception:

        class MyDivisionError(ZeroDivisionError):
            pass

        sentry_init(ignore_errors=[ZeroDivisionError], transport=_TestTransport())

        def e(exc):
            try:
                raise exc
            except Exception:
                capture_exception()

        e(ZeroDivisionError())
        e(MyDivisionError())
        e(ValueError())

        assert mock_capture_internal_exception.call_count == 1
        assert (
            mock_capture_internal_exception.call_args[0][0][0] == EnvelopeCapturedError
        )


def test_include_local_variables_enabled(sentry_init, capture_events):
    sentry_init(include_local_variables=True)
    events = capture_events()
    try:
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    assert all(
        frame["vars"]
        for frame in event["exception"]["values"][0]["stacktrace"]["frames"]
    )


def test_include_local_variables_disabled(sentry_init, capture_events):
    sentry_init(include_local_variables=False)
    events = capture_events()
    try:
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    assert all(
        "vars" not in frame
        for frame in event["exception"]["values"][0]["stacktrace"]["frames"]
    )


def test_include_source_context_enabled(sentry_init, capture_events):
    sentry_init(include_source_context=True)
    events = capture_events()
    try:
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    frame = event["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "post_context" in frame
    assert "pre_context" in frame
    assert "context_line" in frame


def test_include_source_context_disabled(sentry_init, capture_events):
    sentry_init(include_source_context=False)
    events = capture_events()
    try:
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    frame = event["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "post_context" not in frame
    assert "pre_context" not in frame
    assert "context_line" not in frame


@pytest.mark.parametrize("integrations", [[], [ExecutingIntegration()]])
def test_function_names(sentry_init, capture_events, integrations):
    sentry_init(integrations=integrations)
    events = capture_events()

    def foo():
        try:
            bar()
        except Exception:
            capture_exception()

    def bar():
        1 / 0

    foo()

    (event,) = events
    (thread,) = event["exception"]["values"]
    functions = [x["function"] for x in thread["stacktrace"]["frames"]]

    if integrations:
        assert functions == [
            "test_function_names.<locals>.foo",
            "test_function_names.<locals>.bar",
        ]
    else:
        assert functions == ["foo", "bar"]


def test_attach_stacktrace_enabled(sentry_init, capture_events):
    sentry_init(attach_stacktrace=True)
    events = capture_events()

    def foo():
        bar()

    def bar():
        capture_message("HI")

    foo()

    (event,) = events
    (thread,) = event["threads"]["values"]
    functions = [x["function"] for x in thread["stacktrace"]["frames"]]

    assert functions[-2:] == ["foo", "bar"]


def test_attach_stacktrace_enabled_no_locals(sentry_init, capture_events):
    sentry_init(attach_stacktrace=True, include_local_variables=False)
    events = capture_events()

    def foo():
        bar()

    def bar():
        capture_message("HI")

    foo()

    (event,) = events
    (thread,) = event["threads"]["values"]
    local_vars = [x.get("vars") for x in thread["stacktrace"]["frames"]]
    assert local_vars[-2:] == [None, None]


def test_attach_stacktrace_in_app(sentry_init, capture_events):
    sentry_init(attach_stacktrace=True, in_app_exclude=["_pytest"])
    events = capture_events()

    capture_message("hi")

    (event,) = events
    (thread,) = event["threads"]["values"]
    frames = thread["stacktrace"]["frames"]
    pytest_frames = [f for f in frames if f["module"].startswith("_pytest")]
    assert pytest_frames
    assert all(f["in_app"] is False for f in pytest_frames)


def test_attach_stacktrace_disabled(sentry_init, capture_events):
    sentry_init(attach_stacktrace=False)
    events = capture_events()
    capture_message("HI")

    (event,) = events
    assert "threads" not in event


def test_capture_event_works(sentry_init):
    sentry_init(transport=_TestTransport())
    pytest.raises(EnvelopeCapturedError, lambda: capture_event({}))
    pytest.raises(EnvelopeCapturedError, lambda: capture_event({}))


@pytest.mark.parametrize("num_messages", [10, 20])
def test_atexit(tmpdir, monkeypatch, num_messages):
    app = tmpdir.join("app.py")
    app.write(
        dedent(
            """
    import time
    from sentry_sdk import init, transport, capture_message

    def capture_envelope(self, envelope):
        time.sleep(0.1)
        event = envelope.get_event() or dict()
        message = event.get("message", "")
        print(message)

    transport.HttpTransport.capture_envelope = capture_envelope
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
    """
    Test that scope is configured if client is configured

    This test can be removed once configure_scope and the Hub are removed.
    """
    sentry_init()

    with configure_scope() as scope:
        assert scope is Hub.current.scope
        scope.set_tag("foo", "bar")

    calls = []

    def callback(scope):
        calls.append(scope)
        scope.set_tag("foo", "bar")

    assert configure_scope(callback) is None
    assert len(calls) == 1
    assert calls[0] is Hub.current.scope


@pytest.mark.tests_internal_exceptions
def test_client_debug_option_enabled(sentry_init, caplog):
    sentry_init(debug=True)

    sentry_sdk.Scope.get_isolation_scope()._capture_internal_exception(
        (ValueError, ValueError("OK"), None)
    )
    assert "OK" in caplog.text


@pytest.mark.tests_internal_exceptions
@pytest.mark.parametrize("with_client", (True, False))
def test_client_debug_option_disabled(with_client, sentry_init, caplog):
    if with_client:
        sentry_init()

    sentry_sdk.Scope.get_isolation_scope()._capture_internal_exception(
        (ValueError, ValueError("OK"), None)
    )
    assert "OK" not in caplog.text


@pytest.mark.skip(
    reason="New behavior in SDK 2.0: You have a scope before init and add data to it."
)
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
    (event,) = events

    assert "tags" not in event


def test_weird_chars(sentry_init, capture_events):
    sentry_init()
    events = capture_events()
    capture_message("föö".encode("latin1"))
    (event,) = events
    assert json.loads(json.dumps(event)) == event


def test_nan(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        # should_repr_strings=False
        set_tag("mynan", float("nan"))

        # should_repr_strings=True
        nan = float("nan")  # noqa
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    (frame,) = frames
    assert frame["vars"]["nan"] == "nan"
    assert event["tags"]["mynan"] == "nan"


def test_cyclic_frame_vars(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        a = {}
        a["a"] = a
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events
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
    (event,) = events

    data = event["extra"]["foo"]
    assert data == {"not_cyclic2": "", "not_cyclic": "", "is_cyclic": "<cyclic>"}


def test_databag_depth_stripping(sentry_init, capture_events, benchmark):
    sentry_init()
    events = capture_events()

    value = ["a"]
    for _ in range(100000):
        value = [value]

    @benchmark
    def inner():
        del events[:]
        try:
            a = value  # noqa
            1 / 0
        except Exception:
            capture_exception()

        (event,) = events

        assert len(json.dumps(event)) < 10000


def test_databag_string_stripping(sentry_init, capture_events, benchmark):
    sentry_init()
    events = capture_events()

    @benchmark
    def inner():
        del events[:]
        try:
            a = "A" * 1000000  # noqa
            1 / 0
        except Exception:
            capture_exception()

        (event,) = events

        assert len(json.dumps(event)) < 10000


def test_databag_breadth_stripping(sentry_init, capture_events, benchmark):
    sentry_init()
    events = capture_events()

    @benchmark
    def inner():
        del events[:]
        try:
            a = ["a"] * 1000000  # noqa
            1 / 0
        except Exception:
            capture_exception()

        (event,) = events

        assert (
            len(event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["a"])
            == MAX_DATABAG_BREADTH
        )
        assert len(json.dumps(event)) < 10000


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

    (event,) = events

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

    (event,) = events
    assert (
        event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["a"]
        == "<failed to serialize, use init(debug=True) to see error logs>"
    )


def test_mapping_sends_exception(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    class C(Mapping):
        def __iter__(self):
            try:
                1 / 0
            except ZeroDivisionError:
                capture_exception()
            yield "hi"

        def __len__(self):
            """List length"""
            return 1

        def __getitem__(self, ii):
            """Get a list item"""
            if ii == "hi":
                return "hi"

            raise KeyError()

    try:
        a = C()  # noqa
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    assert event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["a"] == {
        "hi": "'hi'"
    }


def test_object_sends_exception(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    class C:
        def __repr__(self):
            try:
                1 / 0
            except ZeroDivisionError:
                capture_exception()
            return "hi, i am a repr"

    try:
        a = C()  # noqa
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    assert (
        event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["a"]
        == "hi, i am a repr"
    )


def test_errno_errors(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    class FooError(Exception):
        errno = 69

    capture_exception(FooError())

    (event,) = events

    (exception,) = event["exception"]["values"]
    assert exception["mechanism"]["meta"]["errno"]["number"] == 69


def test_non_string_variables(sentry_init, capture_events):
    """There is some extremely terrible code in the wild that
    inserts non-strings as variable names into `locals()`."""

    sentry_init()
    events = capture_events()

    try:
        locals()[42] = True
        1 / 0
    except ZeroDivisionError:
        capture_exception()

    (event,) = events

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    (frame,) = exception["stacktrace"]["frames"]
    assert frame["vars"]["42"] == "True"


def test_dict_changed_during_iteration(sentry_init, capture_events):
    """
    Some versions of Bottle modify the WSGI environment inside of this __repr__
    impl: https://github.com/bottlepy/bottle/blob/0.12.16/bottle.py#L1386

    See https://github.com/getsentry/sentry-python/pull/298 for discussion
    """
    sentry_init(send_default_pii=True)
    events = capture_events()

    class TooSmartClass:
        def __init__(self, environ):
            self.environ = environ

        def __repr__(self):
            if "my_representation" in self.environ:
                return self.environ["my_representation"]

            self.environ["my_representation"] = "<This is me>"
            return self.environ["my_representation"]

    try:
        environ = {}
        environ["a"] = TooSmartClass(environ)
        1 / 0
    except ZeroDivisionError:
        capture_exception()

    (event,) = events
    (exception,) = event["exception"]["values"]
    (frame,) = exception["stacktrace"]["frames"]
    assert frame["vars"]["environ"] == {"a": "<This is me>"}


@pytest.mark.parametrize(
    "dsn",
    [
        "http://894b7d594095440f8dfea9b300e6f572@localhost:8000/2",
        "http://894b7d594095440f8dfea9b300e6f572@localhost:8000/2",
    ],
)
def test_init_string_types(dsn, sentry_init):
    # Allow unicode strings on Python 3 and both on Python 2 (due to
    # unicode_literals)
    #
    # Supporting bytes on Python 3 is not really wrong but probably would be
    # extra code
    sentry_init(dsn)
    assert (
        sentry_sdk.get_client().dsn
        == "http://894b7d594095440f8dfea9b300e6f572@localhost:8000/2"
    )


@pytest.mark.parametrize(
    "sdk_options, expected_breadcrumbs",
    [({}, DEFAULT_MAX_BREADCRUMBS), ({"max_breadcrumbs": 50}, 50)],
)
def test_max_breadcrumbs_option(
    sentry_init, capture_events, sdk_options, expected_breadcrumbs
):
    sentry_init(sdk_options)
    events = capture_events()

    for _ in range(1231):
        add_breadcrumb({"type": "sourdough"})

    capture_message("dogs are great")

    assert len(events[0]["breadcrumbs"]["values"]) == expected_breadcrumbs


def test_multiple_positional_args(sentry_init):
    with pytest.raises(TypeError) as exinfo:
        sentry_init(1, None)
    assert "Only single positional argument is expected" in str(exinfo.value)


@pytest.mark.parametrize(
    "sdk_options, expected_data_length",
    [
        ({}, DEFAULT_MAX_VALUE_LENGTH),
        ({"max_value_length": 1800}, 1800),
    ],
)
def test_max_value_length_option(
    sentry_init, capture_events, sdk_options, expected_data_length
):
    sentry_init(sdk_options)
    events = capture_events()

    capture_message("a" * 2000)

    assert len(events[0]["message"]) == expected_data_length


@pytest.mark.parametrize(
    "client_option,env_var_value,debug_output_expected",
    [
        (None, "", False),
        (None, "t", True),
        (None, "1", True),
        (None, "True", True),
        (None, "true", True),
        (None, "f", False),
        (None, "0", False),
        (None, "False", False),
        (None, "false", False),
        (None, "xxx", False),
        (True, "", True),
        (True, "t", True),
        (True, "1", True),
        (True, "True", True),
        (True, "true", True),
        (True, "f", True),
        (True, "0", True),
        (True, "False", True),
        (True, "false", True),
        (True, "xxx", True),
        (False, "", False),
        (False, "t", False),
        (False, "1", False),
        (False, "True", False),
        (False, "true", False),
        (False, "f", False),
        (False, "0", False),
        (False, "False", False),
        (False, "false", False),
        (False, "xxx", False),
    ],
)
@pytest.mark.tests_internal_exceptions
def test_debug_option(
    sentry_init,
    monkeypatch,
    caplog,
    client_option,
    env_var_value,
    debug_output_expected,
):
    monkeypatch.setenv("SENTRY_DEBUG", env_var_value)

    if client_option is None:
        sentry_init()
    else:
        sentry_init(debug=client_option)

    sentry_sdk.Scope.get_isolation_scope()._capture_internal_exception(
        (ValueError, ValueError("something is wrong"), None)
    )
    if debug_output_expected:
        assert "something is wrong" in caplog.text
    else:
        assert "something is wrong" not in caplog.text


class IssuesSamplerTestConfig:
    def __init__(
        self,
        expected_events,
        sampler_function=None,
        sample_rate=None,
        exception_to_raise=Exception,
    ):
        # type: (int, Optional[Callable[[Event], Union[float, bool]]], Optional[float], type[Exception]) -> None
        self.sampler_function_mock = (
            None
            if sampler_function is None
            else mock.MagicMock(side_effect=sampler_function)
        )
        self.expected_events = expected_events
        self.sample_rate = sample_rate
        self.exception_to_raise = exception_to_raise

    def init_sdk(self, sentry_init):
        # type: (Callable[[*Any], None]) -> None
        sentry_init(
            error_sampler=self.sampler_function_mock, sample_rate=self.sample_rate
        )

    def raise_exception(self):
        # type: () -> None
        raise self.exception_to_raise()


@mock.patch("sentry_sdk.client.random.random", return_value=0.618)
@pytest.mark.parametrize(
    "test_config",
    (
        # Baseline test with error_sampler only, both floats and bools
        IssuesSamplerTestConfig(sampler_function=lambda *_: 1.0, expected_events=1),
        IssuesSamplerTestConfig(sampler_function=lambda *_: 0.7, expected_events=1),
        IssuesSamplerTestConfig(sampler_function=lambda *_: 0.6, expected_events=0),
        IssuesSamplerTestConfig(sampler_function=lambda *_: 0.0, expected_events=0),
        IssuesSamplerTestConfig(sampler_function=lambda *_: True, expected_events=1),
        IssuesSamplerTestConfig(sampler_function=lambda *_: False, expected_events=0),
        # Baseline test with sample_rate only
        IssuesSamplerTestConfig(sample_rate=1.0, expected_events=1),
        IssuesSamplerTestConfig(sample_rate=0.7, expected_events=1),
        IssuesSamplerTestConfig(sample_rate=0.6, expected_events=0),
        IssuesSamplerTestConfig(sample_rate=0.0, expected_events=0),
        # error_sampler takes precedence over sample_rate
        IssuesSamplerTestConfig(
            sampler_function=lambda *_: 1.0, sample_rate=0.0, expected_events=1
        ),
        IssuesSamplerTestConfig(
            sampler_function=lambda *_: 0.0, sample_rate=1.0, expected_events=0
        ),
        # Different sample rates based on exception, retrieved both from event and hint
        IssuesSamplerTestConfig(
            sampler_function=lambda event, _: {
                "ZeroDivisionError": 1.0,
                "AttributeError": 0.0,
            }[event["exception"]["values"][0]["type"]],
            exception_to_raise=ZeroDivisionError,
            expected_events=1,
        ),
        IssuesSamplerTestConfig(
            sampler_function=lambda event, _: {
                "ZeroDivisionError": 1.0,
                "AttributeError": 0.0,
            }[event["exception"]["values"][0]["type"]],
            exception_to_raise=AttributeError,
            expected_events=0,
        ),
        IssuesSamplerTestConfig(
            sampler_function=lambda _, hint: {
                ZeroDivisionError: 1.0,
                AttributeError: 0.0,
            }[hint["exc_info"][0]],
            exception_to_raise=ZeroDivisionError,
            expected_events=1,
        ),
        IssuesSamplerTestConfig(
            sampler_function=lambda _, hint: {
                ZeroDivisionError: 1.0,
                AttributeError: 0.0,
            }[hint["exc_info"][0]],
            exception_to_raise=AttributeError,
            expected_events=0,
        ),
        # If sampler returns invalid value, we should still send the event
        IssuesSamplerTestConfig(
            sampler_function=lambda *_: "This is an invalid return value for the sampler",
            expected_events=1,
        ),
    ),
)
def test_error_sampler(_, sentry_init, capture_events, test_config):
    test_config.init_sdk(sentry_init)

    events = capture_events()

    try:
        test_config.raise_exception()
    except Exception:
        capture_exception()

    assert len(events) == test_config.expected_events

    if test_config.sampler_function_mock is not None:
        assert test_config.sampler_function_mock.call_count == 1

        # Ensure two arguments (the event and hint) were passed to the sampler function
        assert len(test_config.sampler_function_mock.call_args[0]) == 2


@pytest.mark.forked
@pytest.mark.parametrize(
    "opt,missing_flags",
    [
        # lazy mode with enable-threads, no warning
        [{"enable-threads": True, "lazy-apps": True}, []],
        [{"enable-threads": "true", "lazy-apps": b"1"}, []],
        # preforking mode with enable-threads and py-call-uwsgi-fork-hooks, no warning
        [{"enable-threads": True, "py-call-uwsgi-fork-hooks": True}, []],
        [{"enable-threads": b"true", "py-call-uwsgi-fork-hooks": b"on"}, []],
        # lazy mode, no enable-threads, warning
        [{"lazy-apps": True}, ["--enable-threads"]],
        [{"enable-threads": b"false", "lazy-apps": True}, ["--enable-threads"]],
        [{"enable-threads": b"0", "lazy": True}, ["--enable-threads"]],
        # preforking mode, no enable-threads or py-call-uwsgi-fork-hooks, warning
        [{}, ["--enable-threads", "--py-call-uwsgi-fork-hooks"]],
        [{"processes": b"2"}, ["--enable-threads", "--py-call-uwsgi-fork-hooks"]],
        [{"enable-threads": True}, ["--py-call-uwsgi-fork-hooks"]],
        [{"enable-threads": b"1"}, ["--py-call-uwsgi-fork-hooks"]],
        [
            {"enable-threads": b"false"},
            ["--enable-threads", "--py-call-uwsgi-fork-hooks"],
        ],
        [{"py-call-uwsgi-fork-hooks": True}, ["--enable-threads"]],
    ],
)
def test_uwsgi_warnings(sentry_init, recwarn, opt, missing_flags):
    uwsgi = mock.MagicMock()
    uwsgi.opt = opt
    with mock.patch.dict("sys.modules", uwsgi=uwsgi):
        sentry_init(profiles_sample_rate=1.0)
        if missing_flags:
            assert len(recwarn) == 1
            record = recwarn.pop()
            for flag in missing_flags:
                assert flag in str(record.message)
        else:
            assert not recwarn

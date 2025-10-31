import contextlib
import os
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
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
    start_transaction,
)
from sentry_sdk.spotlight import DEFAULT_SPOTLIGHT_URL
from sentry_sdk.utils import capture_internal_exception
from sentry_sdk.integrations.executing import ExecutingIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.serializer import MAX_DATABAG_BREADTH
from sentry_sdk.consts import DEFAULT_MAX_BREADCRUMBS, DEFAULT_MAX_VALUE_LENGTH

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional, Union
    from sentry_sdk._types import Event


maximum_python_312 = pytest.mark.skipif(
    sys.version_info > (3, 12),
    reason="Since Python 3.13, `FrameLocalsProxy` skips items of `locals()` that have non-`str` keys; this is a CPython implementation detail: https://github.com/python/cpython/blame/7b413952e817ae87bfda2ac85dd84d30a6ce743b/Objects/frameobject.c#L148",
)


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
@pytest.mark.parametrize(
    "http2", [True, False] if sys.version_info >= (3, 8) else [False]
)
def test_proxy(monkeypatch, testcase, http2):
    if testcase["env_http_proxy"] is not None:
        monkeypatch.setenv("HTTP_PROXY", testcase["env_http_proxy"])
    if testcase["env_https_proxy"] is not None:
        monkeypatch.setenv("HTTPS_PROXY", testcase["env_https_proxy"])
    if testcase.get("env_no_proxy") is not None:
        monkeypatch.setenv("NO_PROXY", testcase["env_no_proxy"])

    kwargs = {}

    if http2:
        kwargs["_experiments"] = {"transport_http2": True}

    if testcase["arg_http_proxy"] is not None:
        kwargs["http_proxy"] = testcase["arg_http_proxy"]
    if testcase["arg_https_proxy"] is not None:
        kwargs["https_proxy"] = testcase["arg_https_proxy"]
    if testcase.get("arg_proxy_headers") is not None:
        kwargs["proxy_headers"] = testcase["arg_proxy_headers"]

    client = Client(testcase["dsn"], **kwargs)

    proxy = getattr(
        client.transport._pool,
        "proxy",
        getattr(client.transport._pool, "_proxy_url", None),
    )
    if testcase["expected_proxy_scheme"] is None:
        assert proxy is None
    else:
        scheme = (
            proxy.scheme.decode("ascii")
            if isinstance(proxy.scheme, bytes)
            else proxy.scheme
        )
        assert scheme == testcase["expected_proxy_scheme"]

        if testcase.get("arg_proxy_headers") is not None:
            proxy_headers = (
                dict(
                    (k.decode("ascii"), v.decode("ascii"))
                    for k, v in client.transport._pool._proxy_headers
                )
                if http2
                else client.transport._pool.proxy_headers
            )
            assert proxy_headers == testcase["arg_proxy_headers"]


@pytest.mark.parametrize(
    "testcase",
    [
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "http://localhost/123",
            "arg_https_proxy": None,
            "should_be_socks_proxy": False,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks4a://localhost/123",
            "arg_https_proxy": None,
            "should_be_socks_proxy": True,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks4://localhost/123",
            "arg_https_proxy": None,
            "should_be_socks_proxy": True,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks5h://localhost/123",
            "arg_https_proxy": None,
            "should_be_socks_proxy": True,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": "socks5://localhost/123",
            "arg_https_proxy": None,
            "should_be_socks_proxy": True,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks4a://localhost/123",
            "should_be_socks_proxy": True,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks4://localhost/123",
            "should_be_socks_proxy": True,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks5h://localhost/123",
            "should_be_socks_proxy": True,
        },
        {
            "dsn": "https://foo@sentry.io/123",
            "arg_http_proxy": None,
            "arg_https_proxy": "socks5://localhost/123",
            "should_be_socks_proxy": True,
        },
    ],
)
@pytest.mark.parametrize(
    "http2", [True, False] if sys.version_info >= (3, 8) else [False]
)
def test_socks_proxy(testcase, http2):
    kwargs = {}

    if http2:
        kwargs["_experiments"] = {"transport_http2": True}

    if testcase["arg_http_proxy"] is not None:
        kwargs["http_proxy"] = testcase["arg_http_proxy"]
    if testcase["arg_https_proxy"] is not None:
        kwargs["https_proxy"] = testcase["arg_https_proxy"]

    client = Client(testcase["dsn"], **kwargs)
    assert ("socks" in str(type(client.transport._pool)).lower()) == testcase[
        "should_be_socks_proxy"
    ], (
        f"Expected {kwargs} to result in SOCKS == {testcase['should_be_socks_proxy']}"
        f"but got {str(type(client.transport._pool))}"
    )


def test_simple_transport(sentry_init):
    events = []
    sentry_init(transport=events.append)
    capture_message("Hello World!")
    assert events[0]["message"] == "Hello World!"


def test_ignore_errors(sentry_init, capture_events):
    sentry_init(ignore_errors=[ZeroDivisionError])
    events = capture_events()

    class MyDivisionError(ZeroDivisionError):
        pass

    def e(exc):
        try:
            raise exc
        except Exception:
            capture_exception()

    e(ZeroDivisionError())
    e(MyDivisionError())
    e(ValueError())

    assert len(events) == 1
    assert events[0]["exception"]["values"][0]["type"] == "ValueError"


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


def test_attach_stacktrace_transaction(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, attach_stacktrace=True)
    events = capture_events()
    with start_transaction(name="transaction"):
        pass
    (event,) = events
    assert "threads" not in event


def test_capture_event_works(sentry_init):
    sentry_init(transport=_TestTransport())
    pytest.raises(EnvelopeCapturedError, lambda: capture_event({}))
    pytest.raises(EnvelopeCapturedError, lambda: capture_event({}))


@pytest.mark.parametrize("num_messages", [10, 20])
@pytest.mark.parametrize(
    "http2", [True, False] if sys.version_info >= (3, 8) else [False]
)
def test_atexit(tmpdir, monkeypatch, num_messages, http2):
    if http2:
        options = '_experiments={"transport_http2": True}'
        transport = "Http2Transport"
    else:
        options = ""
        transport = "HttpTransport"

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

    transport.{transport}.capture_envelope = capture_envelope
    init("http://foobar@localhost/123", shutdown_timeout={num_messages}, {options})

    for _ in range({num_messages}):
        capture_message("HI")
    """.format(transport=transport, options=options, num_messages=num_messages)
        )
    )

    start = time.time()
    output = subprocess.check_output([sys.executable, str(app)])
    end = time.time()

    # Each message takes at least 0.1 seconds to process
    assert int(end - start) >= num_messages / 10

    assert output.count(b"HI") == num_messages


def test_configure_scope_available(
    sentry_init, request, monkeypatch, suppress_deprecation_warnings
):
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

    capture_internal_exception((ValueError, ValueError("OK"), None))
    assert "OK" in caplog.text


@pytest.mark.tests_internal_exceptions
@pytest.mark.parametrize("with_client", (True, False))
def test_client_debug_option_disabled(with_client, sentry_init, caplog):
    if with_client:
        sentry_init()

    capture_internal_exception((ValueError, ValueError("OK"), None))
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

    data = {}
    data["is_cyclic"] = data

    other_data = ""
    data["not_cyclic"] = other_data
    data["not_cyclic2"] = other_data
    sentry_sdk.get_isolation_scope().set_extra("foo", data)

    capture_message("hi")
    (event,) = events

    data = event["extra"]["foo"]
    assert data == {"not_cyclic2": "", "not_cyclic": "", "is_cyclic": "<cyclic>"}


def test_databag_depth_stripping(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    value = ["a"]
    for _ in range(100000):
        value = [value]

    del events[:]
    try:
        a = value  # noqa
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    stacktrace_frame = event["exception"]["values"][0]["stacktrace"]["frames"][0]
    a_var = stacktrace_frame["vars"]["a"]

    assert type(a_var) == list
    assert len(a_var) == 1 and type(a_var[0]) == list

    first_level_list = a_var[0]
    assert type(first_level_list) == list
    assert len(first_level_list) == 1

    second_level_list = first_level_list[0]
    assert type(second_level_list) == list
    assert len(second_level_list) == 1

    third_level_list = second_level_list[0]
    assert type(third_level_list) == list
    assert len(third_level_list) == 1

    inner_value_repr = third_level_list[0]
    assert type(inner_value_repr) == str


def test_databag_string_stripping(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    del events[:]
    try:
        a = "A" * DEFAULT_MAX_VALUE_LENGTH * 10  # noqa
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    assert len(json.dumps(event)) < DEFAULT_MAX_VALUE_LENGTH * 10


def test_databag_breadth_stripping(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

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


@maximum_python_312
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


def test_custom_repr_on_vars(sentry_init, capture_events):
    class Foo:
        pass

    class Fail:
        pass

    def custom_repr(value):
        if isinstance(value, Foo):
            return "custom repr"
        elif isinstance(value, Fail):
            raise ValueError("oops")
        else:
            return None

    sentry_init(custom_repr=custom_repr)
    events = capture_events()

    try:
        my_vars = {"foo": Foo(), "fail": Fail(), "normal": 42}
        1 / 0
    except ZeroDivisionError:
        capture_exception()

    (event,) = events
    (exception,) = event["exception"]["values"]
    (frame,) = exception["stacktrace"]["frames"]
    my_vars = frame["vars"]["my_vars"]
    assert my_vars["foo"] == "custom repr"
    assert my_vars["normal"] == "42"
    assert "Fail object" in my_vars["fail"]


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
        (
            {"max_value_length": DEFAULT_MAX_VALUE_LENGTH + 1000},
            DEFAULT_MAX_VALUE_LENGTH + 1000,
        ),
    ],
)
def test_max_value_length_option(
    sentry_init, capture_events, sdk_options, expected_data_length
):
    sentry_init(sdk_options)
    events = capture_events()

    capture_message("a" * (DEFAULT_MAX_VALUE_LENGTH + 2000))

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

    capture_internal_exception((ValueError, ValueError("something is wrong"), None))
    if debug_output_expected:
        assert "something is wrong" in caplog.text
    else:
        assert "something is wrong" not in caplog.text


@pytest.mark.parametrize(
    "client_option,env_var_value,spotlight_url_expected",
    [
        (None, None, None),
        (None, "", None),
        (None, "F", None),
        (False, None, None),
        (False, "", None),
        (False, "t", None),
        (None, "t", DEFAULT_SPOTLIGHT_URL),
        (None, "1", DEFAULT_SPOTLIGHT_URL),
        (True, None, DEFAULT_SPOTLIGHT_URL),
        (True, "http://localhost:8080/slurp", DEFAULT_SPOTLIGHT_URL),
        ("http://localhost:8080/slurp", "f", "http://localhost:8080/slurp"),
        (None, "http://localhost:8080/slurp", "http://localhost:8080/slurp"),
    ],
)
def test_spotlight_option(
    sentry_init,
    monkeypatch,
    client_option,
    env_var_value,
    spotlight_url_expected,
):
    if env_var_value is None:
        monkeypatch.delenv("SENTRY_SPOTLIGHT", raising=False)
    else:
        monkeypatch.setenv("SENTRY_SPOTLIGHT", env_var_value)

    if client_option is None:
        sentry_init()
    else:
        sentry_init(spotlight=client_option)

    client = sentry_sdk.get_client()
    url = client.spotlight.url if client.spotlight else None
    assert url == spotlight_url_expected, (
        f"With config {client_option} and env {env_var_value}"
    )


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


class TestSpanClientReports:
    """
    Tests for client reports related to spans.
    """

    __test__ = False

    @staticmethod
    def span_dropper(spans_to_drop):
        """
        Returns a function that can be used to drop spans from an event.
        """

        def drop_spans(event, _):
            event["spans"] = event["spans"][spans_to_drop:]
            return event

        return drop_spans

    @staticmethod
    def mock_transaction_event(span_count):
        """
        Returns a mock transaction event with the given number of spans.
        """

        return defaultdict(
            mock.MagicMock,
            type="transaction",
            spans=[mock.MagicMock() for _ in range(span_count)],
        )

    def __init__(self, span_count):
        """Configures a test case with the number of spans dropped and whether the transaction was dropped."""
        self.span_count = span_count
        self.expected_record_lost_event_calls = Counter()
        self.before_send = lambda event, _: event
        self.event_processor = lambda event, _: event

    def _update_resulting_calls(self, reason, drops_transactions=0, drops_spans=0):
        """
        Updates the expected calls with the given resulting calls.
        """
        if drops_transactions > 0:
            self.expected_record_lost_event_calls[
                (reason, "transaction", None, drops_transactions)
            ] += 1

        if drops_spans > 0:
            self.expected_record_lost_event_calls[
                (reason, "span", None, drops_spans)
            ] += 1

    def with_before_send(
        self,
        before_send,
        *,
        drops_transactions=0,
        drops_spans=0,
    ):
        self.before_send = before_send
        self._update_resulting_calls(
            "before_send",
            drops_transactions,
            drops_spans,
        )

        return self

    def with_event_processor(
        self,
        event_processor,
        *,
        drops_transactions=0,
        drops_spans=0,
    ):
        self.event_processor = event_processor
        self._update_resulting_calls(
            "event_processor",
            drops_transactions,
            drops_spans,
        )

        return self

    def run(self, sentry_init, capture_record_lost_event_calls):
        """Runs the test case with the configured parameters."""
        sentry_init(before_send_transaction=self.before_send)
        record_lost_event_calls = capture_record_lost_event_calls()

        with sentry_sdk.isolation_scope() as scope:
            scope.add_event_processor(self.event_processor)
            event = self.mock_transaction_event(self.span_count)
            sentry_sdk.get_client().capture_event(event, scope=scope)

        # We use counters to ensure that the calls are made the expected number of times, disregarding order.
        assert Counter(record_lost_event_calls) == self.expected_record_lost_event_calls


@pytest.mark.parametrize(
    "test_config",
    (
        TestSpanClientReports(span_count=10),  # No spans dropped
        TestSpanClientReports(span_count=0).with_before_send(
            lambda e, _: None,
            drops_transactions=1,
            drops_spans=1,
        ),
        TestSpanClientReports(span_count=10).with_before_send(
            lambda e, _: None,
            drops_transactions=1,
            drops_spans=11,
        ),
        TestSpanClientReports(span_count=10).with_before_send(
            TestSpanClientReports.span_dropper(3),
            drops_spans=3,
        ),
        TestSpanClientReports(span_count=10).with_before_send(
            TestSpanClientReports.span_dropper(10),
            drops_spans=10,
        ),
        TestSpanClientReports(span_count=10).with_event_processor(
            lambda e, _: None,
            drops_transactions=1,
            drops_spans=11,
        ),
        TestSpanClientReports(span_count=10).with_event_processor(
            TestSpanClientReports.span_dropper(3),
            drops_spans=3,
        ),
        TestSpanClientReports(span_count=10).with_event_processor(
            TestSpanClientReports.span_dropper(10),
            drops_spans=10,
        ),
        TestSpanClientReports(span_count=10)
        .with_event_processor(
            TestSpanClientReports.span_dropper(3),
            drops_spans=3,
        )
        .with_before_send(
            TestSpanClientReports.span_dropper(5),
            drops_spans=5,
        ),
        TestSpanClientReports(10)
        .with_event_processor(
            TestSpanClientReports.span_dropper(3),
            drops_spans=3,
        )
        .with_before_send(
            lambda e, _: None,
            drops_transactions=1,
            drops_spans=8,  # 3 of the 11 (incl. transaction) spans already dropped
        ),
    ),
)
def test_dropped_transaction(sentry_init, capture_record_lost_event_calls, test_config):
    test_config.run(sentry_init, capture_record_lost_event_calls)


@pytest.mark.parametrize("enable_tracing", [True, False])
def test_enable_tracing_deprecated(sentry_init, enable_tracing):
    with pytest.warns(DeprecationWarning):
        sentry_init(enable_tracing=enable_tracing)


def make_options_transport_cls():
    """Make an options transport class that captures the options passed to it."""
    # We need a unique class for each test so that the options are not
    # shared between tests.

    class OptionsTransport(Transport):
        """Transport that captures the options passed to it."""

        def __init__(self, options):
            super().__init__(options)
            type(self).options = options

        def capture_envelope(self, _):
            pass

    return OptionsTransport


@contextlib.contextmanager
def clear_env_var(name):
    """Helper to clear the a given environment variable,
    and restore it to its original value on exit."""
    old_value = os.environ.pop(name, None)

    try:
        yield
    finally:
        if old_value is not None:
            os.environ[name] = old_value
        elif name in os.environ:
            del os.environ[name]


@pytest.mark.parametrize(
    ("env_value", "arg_value", "expected_value"),
    [
        (None, None, False),  # default
        ("0", None, False),  # env var false
        ("1", None, True),  # env var true
        (None, False, False),  # arg false
        (None, True, True),  # arg true
        # Argument overrides environment variable
        ("0", True, True),  # env false, arg true
        ("1", False, False),  # env true, arg false
    ],
)
def test_keep_alive(env_value, arg_value, expected_value):
    transport_cls = make_options_transport_cls()
    keep_alive_kwarg = {} if arg_value is None else {"keep_alive": arg_value}

    with clear_env_var("SENTRY_KEEP_ALIVE"):
        if env_value is not None:
            os.environ["SENTRY_KEEP_ALIVE"] = env_value

        sentry_sdk.init(
            dsn="http://foo@sentry.io/123",
            transport=transport_cls,
            **keep_alive_kwarg,
        )

    assert transport_cls.options["keep_alive"] is expected_value

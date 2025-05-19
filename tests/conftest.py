import json
import os
import socket
import warnings
from opentelemetry import trace as otel_trace
from opentelemetry.util._once import Once
from threading import Thread
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import pytest
import jsonschema


try:
    import gevent
except ImportError:
    gevent = None

try:
    import eventlet
except ImportError:
    eventlet = None

import sentry_sdk
import sentry_sdk.utils
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations import (  # noqa: F401
    _installed_integrations,
    _processed_integrations,
)
from sentry_sdk.profiler.transaction_profiler import teardown_profiler
from sentry_sdk.profiler.continuous_profiler import teardown_continuous_profiler
from sentry_sdk.transport import Transport
from sentry_sdk.utils import reraise

from tests import _warning_recorder, _warning_recorder_mgr

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional
    from collections.abc import Iterator


SENTRY_EVENT_SCHEMA = "./checkouts/data-schemas/relay/event.schema.json"

if not os.path.isfile(SENTRY_EVENT_SCHEMA):
    SENTRY_EVENT_SCHEMA = None
else:
    with open(SENTRY_EVENT_SCHEMA) as f:
        SENTRY_EVENT_SCHEMA = json.load(f)

try:
    import pytest_benchmark
except ImportError:

    @pytest.fixture
    def benchmark():
        return lambda x: x()

else:
    del pytest_benchmark


from sentry_sdk import scope
from sentry_sdk.opentelemetry.scope import (
    setup_scope_context_management,
    setup_initial_scopes,
)


@pytest.fixture(autouse=True)
def clean_scopes():
    """
    Resets the scopes for every test to avoid leaking data between tests.
    """
    scope._global_scope = None
    scope._isolation_scope.set(None)
    scope._current_scope.set(None)

    setup_initial_scopes()


@pytest.fixture(autouse=True)
def clean_tracer():
    """Reset TracerProvider so that we can set it up from scratch."""
    otel_trace._TRACER_PROVIDER_SET_ONCE = Once()
    otel_trace._TRACER_PROVIDER = None


@pytest.fixture(autouse=True)
def internal_exceptions(request):
    errors = []
    if "tests_internal_exceptions" in request.keywords:
        return

    def _capture_internal_exception(exc_info):
        errors.append(exc_info)

    @request.addfinalizer
    def _():
        # reraise the errors so that this just acts as a pass-through (that
        # happens to keep track of the errors which pass through it)
        for e in errors:
            reraise(*e)

    sentry_sdk.utils.capture_internal_exception = _capture_internal_exception

    return errors


@pytest.fixture(autouse=True, scope="session")
def _capture_internal_warnings():
    yield

    _warning_recorder_mgr.__exit__(None, None, None)
    recorder = _warning_recorder

    for warning in recorder:
        try:
            if isinstance(warning.message, ResourceWarning):
                continue
        except NameError:
            pass

        if "sentry_sdk" not in str(warning.filename) and "sentry-sdk" not in str(
            warning.filename
        ):
            continue

        # pytest-django
        if "getfuncargvalue" in str(warning.message):
            continue

        # Happens when re-initializing the SDK
        if "but it was only enabled on init()" in str(warning.message):
            continue

        # sanic's usage of aiohttp for test client
        if "verify_ssl is deprecated, use ssl=False instead" in str(warning.message):
            continue

        if "getargspec" in str(warning.message) and warning.filename.endswith(
            ("pyramid/config/util.py", "pyramid/config/views.py")
        ):
            continue

        if "isAlive() is deprecated" in str(
            warning.message
        ) and warning.filename.endswith("celery/utils/timer2.py"):
            continue

        if "collections.abc" in str(warning.message) and warning.filename.endswith(
            ("celery/canvas.py", "werkzeug/datastructures.py", "tornado/httputil.py")
        ):
            continue

        # Django 1.7 emits a (seemingly) false-positive warning for our test
        # app and suggests to use a middleware that does not exist in later
        # Django versions.
        if "SessionAuthenticationMiddleware" in str(warning.message):
            continue

        if "Something has already installed a non-asyncio" in str(warning.message):
            continue

        if "dns.hash" in str(warning.message) or "dns/namedict" in warning.filename:
            continue

        raise AssertionError(warning)


@pytest.fixture
def validate_event_schema(tmpdir):
    def inner(event):
        if SENTRY_EVENT_SCHEMA:
            jsonschema.validate(instance=event, schema=SENTRY_EVENT_SCHEMA)

    return inner


@pytest.fixture
def reset_integrations():
    """
    Use with caution, sometimes we really need to start
    with a clean slate to ensure monkeypatching works well,
    but this also means some other stuff will be monkeypatched twice.
    """
    global _installed_integrations, _processed_integrations

    _processed_integrations.clear()
    _installed_integrations.clear()


@pytest.fixture
def uninstall_integration():
    """Use to force the next call to sentry_init to re-install/setup an integration."""

    def inner(identifier):
        _processed_integrations.discard(identifier)
        _installed_integrations.discard(identifier)

    return inner


@pytest.fixture
def sentry_init(request):
    def inner(*a, **kw):
        setup_scope_context_management()
        kw.setdefault("transport", TestTransport())
        client = sentry_sdk.Client(*a, **kw)
        sentry_sdk.get_global_scope().set_client(client)

    if request.node.get_closest_marker("forked"):
        # Do not run isolation if the test is already running in
        # ultimate isolation (seems to be required for celery tests that
        # fork)
        yield inner
    else:
        old_client = sentry_sdk.get_global_scope().client
        try:
            sentry_sdk.get_current_scope().set_client(None)
            yield inner
        finally:
            sentry_sdk.get_global_scope().set_client(old_client)


class TestTransport(Transport):
    def __init__(self):
        Transport.__init__(self)

    def capture_envelope(self, _: Envelope) -> None:
        """No-op capture_envelope for tests"""
        pass


@pytest.fixture
def capture_events(monkeypatch):
    def inner():
        events = []
        test_client = sentry_sdk.get_client()
        old_capture_envelope = test_client.transport.capture_envelope

        def append_event(envelope):
            for item in envelope:
                if item.headers.get("type") in ("event", "transaction"):
                    events.append(item.payload.json)
            return old_capture_envelope(envelope)

        monkeypatch.setattr(test_client.transport, "capture_envelope", append_event)

        return events

    return inner


@pytest.fixture
def capture_envelopes(monkeypatch):
    def inner():
        envelopes = []
        test_client = sentry_sdk.get_client()
        old_capture_envelope = test_client.transport.capture_envelope

        def append_envelope(envelope):
            envelopes.append(envelope)
            return old_capture_envelope(envelope)

        monkeypatch.setattr(test_client.transport, "capture_envelope", append_envelope)

        return envelopes

    return inner


@pytest.fixture
def capture_record_lost_event_calls(monkeypatch):
    def inner():
        calls = []
        test_client = sentry_sdk.get_client()

        def record_lost_event(reason, data_category=None, item=None, *, quantity=1):
            calls.append((reason, data_category, item, quantity))

        monkeypatch.setattr(
            test_client.transport, "record_lost_event", record_lost_event
        )
        return calls

    return inner


@pytest.fixture
def capture_events_forksafe(monkeypatch, capture_events, request):
    def inner():
        capture_events()

        events_r, events_w = os.pipe()
        events_r = os.fdopen(events_r, "rb", 0)
        events_w = os.fdopen(events_w, "wb", 0)

        test_client = sentry_sdk.get_client()

        old_capture_envelope = test_client.transport.capture_envelope

        def append(envelope):
            event = envelope.get_event() or envelope.get_transaction_event()
            if event is not None:
                events_w.write(json.dumps(event).encode("utf-8"))
                events_w.write(b"\n")
            return old_capture_envelope(envelope)

        def flush(timeout=None, callback=None):
            events_w.write(b"flush\n")

        monkeypatch.setattr(test_client.transport, "capture_envelope", append)
        monkeypatch.setattr(test_client, "flush", flush)

        return EventStreamReader(events_r, events_w)

    return inner


class EventStreamReader:
    def __init__(self, read_file, write_file):
        self.read_file = read_file
        self.write_file = write_file

    def read_event(self):
        return json.loads(self.read_file.readline().decode("utf-8"))

    def read_flush(self):
        assert self.read_file.readline() == b"flush\n"


# scope=session ensures that fixture is run earlier
@pytest.fixture(
    scope="session",
    params=[None, "eventlet", "gevent"],
    ids=("threads", "eventlet", "greenlet"),
)
def maybe_monkeypatched_threading(request):
    if request.param == "eventlet":
        if eventlet is None:
            pytest.skip("no eventlet installed")

        try:
            eventlet.monkey_patch()
        except AttributeError as e:
            if "'thread.RLock' object has no attribute" in str(e):
                # https://bitbucket.org/pypy/pypy/issues/2962/gevent-cannot-patch-rlock-under-pypy-27-7
                pytest.skip("https://github.com/eventlet/eventlet/issues/546")
            else:
                raise
    elif request.param == "gevent":
        if gevent is None:
            pytest.skip("no gevent installed")
        try:
            gevent.monkey.patch_all()
        except Exception as e:
            if "_RLock__owner" in str(e):
                pytest.skip("https://github.com/gevent/gevent/issues/1380")
            else:
                raise
    else:
        assert request.param is None

    return request.param


@pytest.fixture
def render_span_tree():
    def inner(event):
        assert event["type"] == "transaction"

        by_parent = {}
        for span in event["spans"]:
            by_parent.setdefault(span["parent_span_id"], []).append(span)

        def render_span(span):
            yield "- op={}: description={}".format(
                json.dumps(span.get("op")), json.dumps(span.get("description"))
            )
            for subspan in by_parent.get(span["span_id"]) or ():
                for line in render_span(subspan):
                    yield "  {}".format(line)

        root_span = event["contexts"]["trace"]

        # Return a list instead of a multiline string because black will know better how to format that
        return "\n".join(render_span(root_span))

    return inner


@pytest.fixture(name="StringContaining")
def string_containing_matcher():
    """
    An object which matches any string containing the substring passed to the
    object at instantiation time.

    Useful for assert_called_with, assert_any_call, etc.

    Used like this:

    >>> f = mock.Mock()
    >>> f("dogs are great")
    >>> f.assert_any_call("dogs") # will raise AssertionError
    Traceback (most recent call last):
        ...
    AssertionError: mock('dogs') call not found
    >>> f.assert_any_call(StringContaining("dogs")) # no AssertionError

    """

    class StringContaining:
        def __init__(self, substring):
            self.substring = substring
            self.valid_types = (str, bytes)

        def __eq__(self, test_string):
            if not isinstance(test_string, self.valid_types):
                return False

            # this is safe even in py2 because as of 2.6, `bytes` exists in py2
            # as an alias for `str`
            if isinstance(test_string, bytes):
                test_string = test_string.decode()

            if len(self.substring) > len(test_string):
                return False

            return self.substring in test_string

        def __ne__(self, test_string):
            return not self.__eq__(test_string)

    return StringContaining


def _safe_is_equal(x, y):
    """
    Compares two values, preferring to use the first's __eq__ method if it
    exists and is implemented.

    Accounts for py2/py3 differences (like ints in py2 not having a __eq__
    method), as well as the incomparability of certain types exposed by using
    raw __eq__ () rather than ==.
    """

    # Prefer using __eq__ directly to ensure that examples like
    #
    #   maisey = Dog()
    #   maisey.name = "Maisey the Dog"
    #   maisey == ObjectDescribedBy(attrs={"name": StringContaining("Maisey")})
    #
    # evaluate to True (in other words, examples where the values in self.attrs
    # might also have custom __eq__ methods; this makes sure those methods get
    # used if possible)
    try:
        is_equal = x.__eq__(y)
    except AttributeError:
        is_equal = NotImplemented

    # this can happen on its own, too (i.e. without an AttributeError being
    # thrown), which is why this is separate from the except block above
    if is_equal == NotImplemented:
        # using == smoothes out weird variations exposed by raw __eq__
        return x == y

    return is_equal


@pytest.fixture(name="DictionaryContaining")
def dictionary_containing_matcher():
    """
    An object which matches any dictionary containing all key-value pairs from
    the dictionary passed to the object at instantiation time.

    Useful for assert_called_with, assert_any_call, etc.

    Used like this:

    >>> f = mock.Mock()
    >>> f({"dogs": "yes", "cats": "maybe"})
    >>> f.assert_any_call({"dogs": "yes"}) # will raise AssertionError
    Traceback (most recent call last):
        ...
    AssertionError: mock({'dogs': 'yes'}) call not found
    >>> f.assert_any_call(DictionaryContaining({"dogs": "yes"})) # no AssertionError
    """

    class DictionaryContaining:
        def __init__(self, subdict):
            self.subdict = subdict

        def __eq__(self, test_dict):
            if not isinstance(test_dict, dict):
                return False

            if len(self.subdict) > len(test_dict):
                return False

            for key, value in self.subdict.items():
                try:
                    test_value = test_dict[key]
                except KeyError:  # missing key
                    return False

                if not _safe_is_equal(value, test_value):
                    return False

            return True

        def __ne__(self, test_dict):
            return not self.__eq__(test_dict)

    return DictionaryContaining


@pytest.fixture(name="ObjectDescribedBy")
def object_described_by_matcher():
    """
    An object which matches any other object with the given properties.

    Available properties currently are "type" (a type object) and "attrs" (a
    dictionary).

    Useful for assert_called_with, assert_any_call, etc.

    Used like this:

    >>> class Dog:
    ...     pass
    ...
    >>> maisey = Dog()
    >>> maisey.name = "Maisey"
    >>> maisey.age = 7
    >>> f = mock.Mock()
    >>> f(maisey)
    >>> f.assert_any_call(ObjectDescribedBy(type=Dog)) # no AssertionError
    >>> f.assert_any_call(ObjectDescribedBy(attrs={"name": "Maisey"})) # no AssertionError
    """

    class ObjectDescribedBy:
        def __init__(self, type=None, attrs=None):
            self.type = type
            self.attrs = attrs

        def __eq__(self, test_obj):
            if self.type:
                if not isinstance(test_obj, self.type):
                    return False

            if self.attrs:
                for attr_name, attr_value in self.attrs.items():
                    try:
                        test_value = getattr(test_obj, attr_name)
                    except AttributeError:  # missing attribute
                        return False

                    if not _safe_is_equal(attr_value, test_value):
                        return False

            return True

        def __ne__(self, test_obj):
            return not self.__eq__(test_obj)

    return ObjectDescribedBy


@pytest.fixture
def teardown_profiling():
    # Make sure that a previous test didn't leave the profiler running
    teardown_profiler()
    teardown_continuous_profiler()

    yield

    # Make sure that to shut down the profiler after the test
    teardown_profiler()
    teardown_continuous_profiler()


@pytest.fixture()
def suppress_deprecation_warnings():
    """
    Use this fixture to suppress deprecation warnings in a test.
    Useful for testing deprecated SDK features.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield


class MockServerRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        # Process an HTTP GET request and return a response.
        # If the path ends with /status/<number>, return status code <number>.
        # Otherwise return a 200 response.
        code = 200
        if "/status/" in self.path:
            code = int(self.path[-3:])

        self.send_response(code)
        self.end_headers()
        return


def get_free_port():
    s = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM)
    s.bind(("localhost", 0))
    _, port = s.getsockname()
    s.close()
    return port


def create_mock_http_server():
    # Start a mock server to test outgoing http requests
    mock_server_port = get_free_port()
    mock_server = HTTPServer(("localhost", mock_server_port), MockServerRequestHandler)
    mock_server_thread = Thread(target=mock_server.serve_forever)
    mock_server_thread.setDaemon(True)
    mock_server_thread.start()

    return mock_server_port


def unpack_werkzeug_response(response):
    # werkzeug < 2.1 returns a tuple as client response, newer versions return
    # an object
    try:
        return response.get_data(), response.status, response.headers
    except AttributeError:
        content, status, headers = response
        return b"".join(content), status, headers


def werkzeug_set_cookie(client, servername, key, value):
    # client.set_cookie has a different signature in different werkzeug versions
    try:
        client.set_cookie(servername, key, value)
    except TypeError:
        client.set_cookie(key, value)


@contextmanager
def patch_start_tracing_child(fake_transaction_is_none=False):
    # type: (bool) -> Iterator[Optional[mock.MagicMock]]
    if not fake_transaction_is_none:
        fake_transaction = mock.MagicMock()
        fake_start_child = mock.MagicMock()
        fake_transaction.start_child = fake_start_child
    else:
        fake_transaction = None
        fake_start_child = None

    with mock.patch(
        "sentry_sdk.tracing_utils.get_current_span", return_value=fake_transaction
    ):
        yield fake_start_child


class ApproxDict(dict):
    def __eq__(self, other):
        # For an ApproxDict to equal another dict, the other dict just needs to contain
        # all the keys from the ApproxDict with the same values.
        #
        # The other dict may contain additional keys with any value.
        return all(key in other and other[key] == value for key, value in self.items())

    def __ne__(self, other):
        return not self.__eq__(other)


class SortedBaggage:
    def __init__(self, baggage):
        self.baggage = baggage

    def __eq__(self, other):
        return sorted(self.baggage.split(",")) == sorted(other.split(","))

    def __ne__(self, other):
        return not self.__eq__(other)

import os
import json

import pytest
import jsonschema

import gevent
import eventlet

import sentry_sdk
from sentry_sdk._compat import reraise, string_types, iteritems
from sentry_sdk.transport import Transport
from sentry_sdk.envelope import Envelope
from sentry_sdk.utils import capture_internal_exceptions

from tests import _warning_recorder, _warning_recorder_mgr


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


@pytest.fixture(autouse=True)
def internal_exceptions(request, monkeypatch):
    errors = []
    if "tests_internal_exceptions" in request.keywords:
        return

    def _capture_internal_exception(self, exc_info):
        errors.append(exc_info)

    @request.addfinalizer
    def _():
        # rerasise the errors so that this just acts as a pass-through (that
        # happens to keep track of the errors which pass through it)
        for e in errors:
            reraise(*e)

    monkeypatch.setattr(
        sentry_sdk.Hub, "_capture_internal_exception", _capture_internal_exception
    )

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
def monkeypatch_test_transport(monkeypatch, validate_event_schema):
    def check_event(event):
        def check_string_keys(map):
            for key, value in iteritems(map):
                assert isinstance(key, string_types)
                if isinstance(value, dict):
                    check_string_keys(value)

        with capture_internal_exceptions():
            check_string_keys(event)
            validate_event_schema(event)

    def check_envelope(envelope):
        with capture_internal_exceptions():
            # Assert error events are sent without envelope to server, for compat.
            # This does not apply if any item in the envelope is an attachment.
            if not any(x.type == "attachment" for x in envelope.items):
                assert not any(item.data_category == "error" for item in envelope.items)
                assert not any(item.get_event() is not None for item in envelope.items)

    def inner(client):
        monkeypatch.setattr(
            client, "transport", TestTransport(check_event, check_envelope)
        )

    return inner


@pytest.fixture
def validate_event_schema(tmpdir):
    def inner(event):
        if SENTRY_EVENT_SCHEMA:
            jsonschema.validate(instance=event, schema=SENTRY_EVENT_SCHEMA)

    return inner


@pytest.fixture
def sentry_init(monkeypatch_test_transport, request):
    def inner(*a, **kw):
        hub = sentry_sdk.Hub.current
        client = sentry_sdk.Client(*a, **kw)
        hub.bind_client(client)
        if "transport" not in kw:
            monkeypatch_test_transport(sentry_sdk.Hub.current.client)

    if request.node.get_closest_marker("forked"):
        # Do not run isolation if the test is already running in
        # ultimate isolation (seems to be required for celery tests that
        # fork)
        yield inner
    else:
        with sentry_sdk.Hub(None):
            yield inner


class TestTransport(Transport):
    def __init__(self, capture_event_callback, capture_envelope_callback):
        Transport.__init__(self)
        self.capture_event = capture_event_callback
        self.capture_envelope = capture_envelope_callback
        self._queue = None


@pytest.fixture
def capture_events(monkeypatch):
    def inner():
        events = []
        test_client = sentry_sdk.Hub.current.client
        old_capture_event = test_client.transport.capture_event
        old_capture_envelope = test_client.transport.capture_envelope

        def append_event(event):
            events.append(event)
            return old_capture_event(event)

        def append_envelope(envelope):
            for item in envelope:
                if item.headers.get("type") in ("event", "transaction"):
                    test_client.transport.capture_event(item.payload.json)
            return old_capture_envelope(envelope)

        monkeypatch.setattr(test_client.transport, "capture_event", append_event)
        monkeypatch.setattr(test_client.transport, "capture_envelope", append_envelope)
        return events

    return inner


@pytest.fixture
def capture_envelopes(monkeypatch):
    def inner():
        envelopes = []
        test_client = sentry_sdk.Hub.current.client
        old_capture_event = test_client.transport.capture_event
        old_capture_envelope = test_client.transport.capture_envelope

        def append_event(event):
            envelope = Envelope()
            envelope.add_event(event)
            envelopes.append(envelope)
            return old_capture_event(event)

        def append_envelope(envelope):
            envelopes.append(envelope)
            return old_capture_envelope(envelope)

        monkeypatch.setattr(test_client.transport, "capture_event", append_event)
        monkeypatch.setattr(test_client.transport, "capture_envelope", append_envelope)
        return envelopes

    return inner


@pytest.fixture
def capture_events_forksafe(monkeypatch, capture_events, request):
    def inner():
        capture_events()

        events_r, events_w = os.pipe()
        events_r = os.fdopen(events_r, "rb", 0)
        events_w = os.fdopen(events_w, "wb", 0)

        test_client = sentry_sdk.Hub.current.client

        old_capture_event = test_client.transport.capture_event

        def append(event):
            events_w.write(json.dumps(event).encode("utf-8"))
            events_w.write(b"\n")
            return old_capture_event(event)

        def flush(timeout=None, callback=None):
            events_w.write(b"flush\n")

        monkeypatch.setattr(test_client.transport, "capture_event", append)
        monkeypatch.setattr(test_client, "flush", flush)

        return EventStreamReader(events_r)

    return inner


class EventStreamReader(object):
    def __init__(self, file):
        self.file = file

    def read_event(self):
        return json.loads(self.file.readline().decode("utf-8"))

    def read_flush(self):
        assert self.file.readline() == b"flush\n"


# scope=session ensures that fixture is run earlier
@pytest.fixture(
    scope="session",
    params=[None, "eventlet", "gevent"],
    ids=("threads", "eventlet", "greenlet"),
)
def maybe_monkeypatched_threading(request):
    if request.param == "eventlet":
        try:
            eventlet.monkey_patch()
        except AttributeError as e:
            if "'thread.RLock' object has no attribute" in str(e):
                # https://bitbucket.org/pypy/pypy/issues/2962/gevent-cannot-patch-rlock-under-pypy-27-7
                pytest.skip("https://github.com/eventlet/eventlet/issues/546")
            else:
                raise
    elif request.param == "gevent":
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

    class StringContaining(object):
        def __init__(self, substring):
            self.substring = substring

        def __eq__(self, test_string):
            if not isinstance(test_string, str):
                return False

            if len(self.substring) > len(test_string):
                return False

            return self.substring in test_string

    return StringContaining


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

    class DictionaryContaining(object):
        def __init__(self, subdict):
            self.subdict = subdict

        def __eq__(self, test_dict):
            if not isinstance(test_dict, dict):
                return False

            if len(self.subdict) > len(test_dict):
                return False

            # Have to test self == other (rather than vice-versa) in case
            # any of the values in self.subdict is another matcher with a custom
            # __eq__ method (in LHS == RHS, LHS's __eq__ is tried before RHS's).
            # In other words, this order is important so that examples like
            # {"dogs": "are great"} == DictionaryContaining({"dogs": StringContaining("great")})
            # evaluate to True
            return all(self.subdict[key] == test_dict.get(key) for key in self.subdict)

    return DictionaryContaining


@pytest.fixture(name="ObjectDescribedBy")
def object_described_by_matcher():
    """
    An object which matches any other object with the given properties.

    Available properties currently are "type" (a type object) and "attrs" (a
    dictionary).

    Useful for assert_called_with, assert_any_call, etc.

    Used like this:

    >>> class Dog(object):
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

    class ObjectDescribedBy(object):
        def __init__(self, type=None, attrs=None):
            self.type = type
            self.attrs = attrs

        def __eq__(self, test_obj):
            if self.type:
                if not isinstance(test_obj, self.type):
                    return False

            # all checks here done with getattr rather than comparing to
            # __dict__ because __dict__ isn't guaranteed to exist
            if self.attrs:
                # attributes must exist AND values must match
                try:
                    if any(
                        getattr(test_obj, attr_name) != attr_value
                        for attr_name, attr_value in self.attrs.items()
                    ):
                        return False  # wrong attribute value
                except AttributeError:  # missing attribute
                    return False

            return True

    return ObjectDescribedBy

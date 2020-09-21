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
                    events.append(item.payload.json)
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
def capture_events_forksafe(monkeypatch):
    def inner():
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

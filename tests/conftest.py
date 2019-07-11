import os
import subprocess
import json

import pytest

import sentry_sdk
from sentry_sdk._compat import reraise, string_types, iteritems
from sentry_sdk.transport import Transport

from tests import _warning_recorder, _warning_recorder_mgr

SEMAPHORE = "./semaphore"

if not os.path.isfile(SEMAPHORE):
    SEMAPHORE = None


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

        raise AssertionError(warning)


@pytest.fixture
def monkeypatch_test_transport(monkeypatch, semaphore_normalize):
    def check_event(event):
        def check_string_keys(map):
            for key, value in iteritems(map):
                assert isinstance(key, string_types)
                if isinstance(value, dict):
                    check_string_keys(value)

        check_string_keys(event)
        semaphore_normalize(event)

    def inner(client):
        monkeypatch.setattr(client, "transport", TestTransport(check_event))

    return inner


def _no_errors_in_semaphore_response(obj):
    """Assert that semaphore didn't throw any errors when processing the
    event."""

    def inner(obj):
        if not isinstance(obj, dict):
            return

        assert "err" not in obj

        for value in obj.values():
            inner(value)

    try:
        inner(obj.get("_meta"))
        inner(obj.get(""))
    except AssertionError:
        raise AssertionError(obj)


@pytest.fixture
def semaphore_normalize(tmpdir):
    def inner(event):
        if not SEMAPHORE:
            return

        # Disable subprocess integration
        with sentry_sdk.Hub(None):
            # not dealing with the subprocess API right now
            file = tmpdir.join("event")
            file.write(json.dumps(dict(event)))
            output = json.loads(
                subprocess.check_output(
                    [SEMAPHORE, "process-event"], stdin=file.open()
                ).decode("utf-8")
            )
            _no_errors_in_semaphore_response(output)
            output.pop("_meta", None)
            return output

    return inner


@pytest.fixture
def sentry_init(monkeypatch_test_transport):
    def inner(*a, **kw):
        hub = sentry_sdk.Hub.current
        client = sentry_sdk.Client(*a, **kw)
        hub.bind_client(client)
        monkeypatch_test_transport(sentry_sdk.Hub.current.client)

    return inner


class TestTransport(Transport):
    def __init__(self, capture_event_callback):
        Transport.__init__(self)
        self.capture_event = capture_event_callback
        self._queue = None


@pytest.fixture
def capture_events(monkeypatch):
    def inner():
        events = []
        test_client = sentry_sdk.Hub.current.client
        old_capture_event = test_client.transport.capture_event

        def append(event):
            events.append(event)
            return old_capture_event(event)

        monkeypatch.setattr(test_client.transport, "capture_event", append)
        return events

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

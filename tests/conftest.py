import os
import subprocess
import json

import pytest

import sentry_sdk
from sentry_sdk._compat import reraise
from sentry_sdk.transport import Transport

SEMAPHORE = "./checkouts/semaphore/target/debug/semaphore"

if not os.path.isfile(SEMAPHORE):
    SEMAPHORE = None


@pytest.fixture(autouse=True)
def reraise_internal_exceptions(request, monkeypatch):
    if "tests_internal_exceptions" in request.keywords:
        return

    def capture_internal_exception(exc_info):
        reraise(*exc_info)

    monkeypatch.setattr(
        sentry_sdk.get_current_hub(),
        "capture_internal_exception",
        capture_internal_exception,
    )


@pytest.fixture
def monkeypatch_test_transport(monkeypatch, assert_semaphore_acceptance):
    def inner(client):
        monkeypatch.setattr(
            client, "_transport", TestTransport(assert_semaphore_acceptance)
        )

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
def assert_semaphore_acceptance(tmpdir):
    if not SEMAPHORE:
        return lambda event: None

    def inner(event):
        # not dealing with the subprocess API right now
        file = tmpdir.join("event")
        file.write(json.dumps(dict(event)))
        output = json.loads(
            subprocess.check_output(
                [SEMAPHORE, "process-event"], stdin=file.open()
            ).decode("utf-8")
        )
        _no_errors_in_semaphore_response(output)

    return inner


@pytest.fixture
def sentry_init(monkeypatch_test_transport, assert_semaphore_acceptance):
    def inner(*a, **kw):
        sentry_sdk.api._init_on_current(*a, **kw)
        monkeypatch_test_transport(sentry_sdk.Hub.current.client)

    return inner


class TestTransport(Transport):
    def __init__(self, capture_event_callback):
        self.capture_event = capture_event_callback
        self._queue = None

    def start(self):
        pass

    def close(self):
        pass

    dsn = "LOL"


@pytest.fixture
def capture_events(monkeypatch):
    def inner():
        events = []
        test_client = sentry_sdk.Hub.current.client
        old_capture_event = test_client._transport.capture_event

        def append(event):
            events.append(event)
            return old_capture_event(event)

        monkeypatch.setattr(test_client._transport, "capture_event", append)
        return events

    return inner

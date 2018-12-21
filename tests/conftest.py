import copy
import os
import subprocess
import json

import pytest

import sentry_sdk
from sentry_sdk._compat import reraise, string_types
from sentry_sdk.transport import Transport

SEMAPHORE = "./semaphore"

if not os.path.isfile(SEMAPHORE):
    SEMAPHORE = None


@pytest.fixture(autouse=True)
def reraise_internal_exceptions(request, monkeypatch):
    if "tests_internal_exceptions" in request.keywords:
        return

    def _capture_internal_exception(exc_info):
        reraise(*exc_info)

    monkeypatch.setattr(
        sentry_sdk.Hub.current,
        "_capture_internal_exception",
        _capture_internal_exception,
    )


@pytest.fixture
def monkeypatch_test_transport(monkeypatch, assert_semaphore_acceptance):
    def check_event(event):
        def check_string_keys(map):
            for key, value in map.items():
                assert isinstance(key, string_types)
                if isinstance(value, dict):
                    check_string_keys(value)

        check_string_keys(event)
        assert_semaphore_acceptance(event)

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
def assert_semaphore_acceptance(tmpdir):
    def inner(event):
        if not SEMAPHORE:
            return
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
def sentry_init(monkeypatch_test_transport, monkeypatch):
    def inner(*a, **kw):
        hub = sentry_sdk.Hub.current
        client = sentry_sdk.Client(*a, **kw)
        monkeypatch.setattr(hub, "_stack", [(client, sentry_sdk.Scope())])
        monkeypatch_test_transport(sentry_sdk.Hub.current.client)

    return inner


@pytest.fixture(autouse=True)
def _assert_no_client(request, monkeypatch):
    assert sentry_sdk.Hub.current.client is None
    assert sentry_sdk.Hub.main.client is None

    @request.addfinalizer
    def _check_no_client():
        monkeypatch.undo()  # tear down sentry_init
        assert sentry_sdk.Hub.current.client is None
        assert sentry_sdk.Hub.main.client is None


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

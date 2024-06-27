import uuid
from unittest import mock

import pytest

import sentry_sdk

from sentry_sdk.crons import capture_checkin


@sentry_sdk.monitor(monitor_slug="abc123")
def _hello_world(name):
    return "Hello, {}".format(name)


@sentry_sdk.monitor(monitor_slug="def456")
def _break_world(name):
    1 / 0
    return "Hello, {}".format(name)


def _hello_world_contextmanager(name):
    with sentry_sdk.monitor(monitor_slug="abc123"):
        return "Hello, {}".format(name)


def _break_world_contextmanager(name):
    with sentry_sdk.monitor(monitor_slug="def456"):
        1 / 0
        return "Hello, {}".format(name)


@sentry_sdk.monitor(monitor_slug="abc123")
async def _hello_world_async(name):
    return "Hello, {}".format(name)


@sentry_sdk.monitor(monitor_slug="def456")
async def _break_world_async(name):
    1 / 0
    return "Hello, {}".format(name)


async def my_coroutine():
    return


async def _hello_world_contextmanager_async(name):
    with sentry_sdk.monitor(monitor_slug="abc123"):
        await my_coroutine()
        return "Hello, {}".format(name)


async def _break_world_contextmanager_async(name):
    with sentry_sdk.monitor(monitor_slug="def456"):
        await my_coroutine()
        1 / 0
        return "Hello, {}".format(name)


@sentry_sdk.monitor(monitor_slug="ghi789", monitor_config=None)
def _no_monitor_config():
    return


@sentry_sdk.monitor(
    monitor_slug="ghi789",
    monitor_config={
        "schedule": {"type": "crontab", "value": "0 0 * * *"},
        "failure_issue_threshold": 5,
    },
)
def _with_monitor_config():
    return


def test_decorator(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        result = _hello_world("Grace")
        assert result == "Hello, Grace"

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="abc123", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "abc123"
        assert fake_capture_checkin.call_args[1]["status"] == "ok"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]


def test_decorator_error(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        with pytest.raises(ZeroDivisionError):
            result = _break_world("Grace")

        assert "result" not in locals()

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="def456", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "def456"
        assert fake_capture_checkin.call_args[1]["status"] == "error"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]


def test_contextmanager(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        result = _hello_world_contextmanager("Grace")
        assert result == "Hello, Grace"

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="abc123", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "abc123"
        assert fake_capture_checkin.call_args[1]["status"] == "ok"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]


def test_contextmanager_error(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        with pytest.raises(ZeroDivisionError):
            result = _break_world_contextmanager("Grace")

        assert "result" not in locals()

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="def456", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "def456"
        assert fake_capture_checkin.call_args[1]["status"] == "error"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]


def test_capture_checkin_simple(sentry_init):
    sentry_init()

    check_in_id = capture_checkin(
        monitor_slug="abc123",
        check_in_id="112233",
        status=None,
        duration=None,
    )
    assert check_in_id == "112233"


def test_sample_rate_doesnt_affect_crons(sentry_init, capture_envelopes):
    sentry_init(sample_rate=0)
    envelopes = capture_envelopes()

    capture_checkin(check_in_id="112233")

    assert len(envelopes) == 1

    check_in = envelopes[0].items[0].payload.json
    assert check_in["check_in_id"] == "112233"


def test_capture_checkin_new_id(sentry_init):
    sentry_init()

    with mock.patch("uuid.uuid4") as mock_uuid:
        mock_uuid.return_value = uuid.UUID("a8098c1a-f86e-11da-bd1a-00112444be1e")
        check_in_id = capture_checkin(
            monitor_slug="abc123",
            check_in_id=None,
            status=None,
            duration=None,
        )

        assert check_in_id == "a8098c1af86e11dabd1a00112444be1e"


def test_end_to_end(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    capture_checkin(
        monitor_slug="abc123",
        check_in_id="112233",
        duration=123,
        status="ok",
    )

    check_in = envelopes[0].items[0].payload.json

    # Check for final checkin
    assert check_in["check_in_id"] == "112233"
    assert check_in["monitor_slug"] == "abc123"
    assert check_in["status"] == "ok"
    assert check_in["duration"] == 123


def test_monitor_config(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    monitor_config = {
        "schedule": {"type": "crontab", "value": "0 0 * * *"},
        "failure_issue_threshold": 5,
        "recovery_threshold": 5,
    }

    capture_checkin(monitor_slug="abc123", monitor_config=monitor_config)
    check_in = envelopes[0].items[0].payload.json

    # Check for final checkin
    assert check_in["monitor_slug"] == "abc123"
    assert check_in["monitor_config"] == monitor_config

    # Without passing a monitor_config the field is not in the checkin
    capture_checkin(monitor_slug="abc123")
    check_in = envelopes[1].items[0].payload.json

    assert check_in["monitor_slug"] == "abc123"
    assert "monitor_config" not in check_in


def test_decorator_monitor_config(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    _with_monitor_config()

    assert len(envelopes) == 2

    for check_in_envelope in envelopes:
        assert len(check_in_envelope.items) == 1
        check_in = check_in_envelope.items[0].payload.json

        assert check_in["monitor_slug"] == "ghi789"
        assert check_in["monitor_config"] == {
            "schedule": {"type": "crontab", "value": "0 0 * * *"},
            "failure_issue_threshold": 5,
        }


def test_decorator_no_monitor_config(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    _no_monitor_config()

    assert len(envelopes) == 2

    for check_in_envelope in envelopes:
        assert len(check_in_envelope.items) == 1
        check_in = check_in_envelope.items[0].payload.json

        assert check_in["monitor_slug"] == "ghi789"
        assert "monitor_config" not in check_in


def test_capture_checkin_sdk_not_initialized():
    # Tests that the capture_checkin does not raise an error when Sentry SDK is not initialized.
    # sentry_init() is intentionally omitted.
    check_in_id = capture_checkin(
        monitor_slug="abc123",
        check_in_id="112233",
        status=None,
        duration=None,
    )
    assert check_in_id == "112233"


def test_scope_data_in_checkin(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    valid_keys = [
        # Mandatory event keys
        "type",
        "event_id",
        "timestamp",
        "platform",
        # Optional event keys
        "release",
        "environment",
        "server_name",
        "sdk",
        # Mandatory check-in specific keys
        "check_in_id",
        "monitor_slug",
        "status",
        # Optional check-in specific keys
        "duration",
        "monitor_config",
        "contexts",  # an event processor adds this
    ]

    # Add some data to the scope
    sentry_sdk.add_breadcrumb(message="test breadcrumb")
    sentry_sdk.set_context("test_context", {"test_key": "test_value"})
    sentry_sdk.set_extra("test_extra", "test_value")
    sentry_sdk.set_level("warning")
    sentry_sdk.set_tag("test_tag", "test_value")

    capture_checkin(
        monitor_slug="abc123",
        check_in_id="112233",
        status="ok",
        duration=123,
    )

    (envelope,) = envelopes
    check_in_event = envelope.items[0].payload.json

    invalid_keys = []
    for key in check_in_event.keys():
        if key not in valid_keys:
            invalid_keys.append(key)

    assert len(invalid_keys) == 0, "Unexpected keys found in checkin: {}".format(
        invalid_keys
    )


@pytest.mark.asyncio
async def test_decorator_async(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        result = await _hello_world_async("Grace")
        assert result == "Hello, Grace"

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="abc123", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "abc123"
        assert fake_capture_checkin.call_args[1]["status"] == "ok"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]


@pytest.mark.asyncio
async def test_decorator_error_async(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        with pytest.raises(ZeroDivisionError):
            result = await _break_world_async("Grace")

        assert "result" not in locals()

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="def456", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "def456"
        assert fake_capture_checkin.call_args[1]["status"] == "error"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]


@pytest.mark.asyncio
async def test_contextmanager_async(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        result = await _hello_world_contextmanager_async("Grace")
        assert result == "Hello, Grace"

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="abc123", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "abc123"
        assert fake_capture_checkin.call_args[1]["status"] == "ok"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]


@pytest.mark.asyncio
async def test_contextmanager_error_async(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        with pytest.raises(ZeroDivisionError):
            result = await _break_world_contextmanager_async("Grace")

        assert "result" not in locals()

        # Check for initial checkin
        fake_capture_checkin.assert_has_calls(
            [
                mock.call(
                    monitor_slug="def456", status="in_progress", monitor_config=None
                ),
            ]
        )

        # Check for final checkin
        assert fake_capture_checkin.call_args[1]["monitor_slug"] == "def456"
        assert fake_capture_checkin.call_args[1]["status"] == "error"
        assert fake_capture_checkin.call_args[1]["duration"]
        assert fake_capture_checkin.call_args[1]["check_in_id"]

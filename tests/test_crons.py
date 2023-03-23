import mock
import pytest
import uuid

import sentry_sdk
from sentry_sdk.crons import capture_checkin


@sentry_sdk.monitor(monitor_slug="abc123")
def _hello_world(name):
    return "Hello, {}".format(name)


@sentry_sdk.monitor(monitor_slug="def456")
def _break_world(name):
    1 / 0
    return "Hello, {}".format(name)


def test_decorator(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checking:
        result = _hello_world("Grace")
        assert result == "Hello, Grace"

        # Check for initial checkin
        fake_capture_checking.assert_has_calls(
            [
                mock.call(monitor_slug="abc123", status="in_progress"),
            ]
        )

        # Check for final checkin
        assert fake_capture_checking.call_args[1]["monitor_slug"] == "abc123"
        assert fake_capture_checking.call_args[1]["status"] == "ok"
        assert fake_capture_checking.call_args[1]["duration"]
        assert fake_capture_checking.call_args[1]["check_in_id"]


def test_decorator_error(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checking:
        with pytest.raises(Exception):
            result = _break_world("Grace")

        assert "result" not in locals()

        # Check for initial checkin
        fake_capture_checking.assert_has_calls(
            [
                mock.call(monitor_slug="def456", status="in_progress"),
            ]
        )

        # Check for final checkin
        assert fake_capture_checking.call_args[1]["monitor_slug"] == "def456"
        assert fake_capture_checking.call_args[1]["status"] == "error"
        assert fake_capture_checking.call_args[1]["duration"]
        assert fake_capture_checking.call_args[1]["check_in_id"]


def test_capture_checkin_simple(sentry_init):
    sentry_init()

    check_in_id = capture_checkin(
        monitor_slug="abc123",
        check_in_id="112233",
        status=None,
        duration=None,
    )
    assert check_in_id == "112233"


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

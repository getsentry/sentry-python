import pytest

import sentry_sdk

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@sentry_sdk.monitor(monitor_slug="abc123")
async def _hello_world(name):
    return "Hello, {}".format(name)


@sentry_sdk.monitor(monitor_slug="def456")
async def _break_world(name):
    1 / 0
    return "Hello, {}".format(name)


async def my_coroutine():
    return


async def _hello_world_contextmanager(name):
    with sentry_sdk.monitor(monitor_slug="abc123"):
        await my_coroutine()
        return "Hello, {}".format(name)


async def _break_world_contextmanager(name):
    with sentry_sdk.monitor(monitor_slug="def456"):
        await my_coroutine()
        1 / 0
        return "Hello, {}".format(name)


@pytest.mark.asyncio
async def test_decorator(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        result = await _hello_world("Grace")
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
async def test_decorator_error(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        with pytest.raises(ZeroDivisionError):
            result = await _break_world("Grace")

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
async def test_contextmanager(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        result = await _hello_world_contextmanager("Grace")
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
async def test_contextmanager_error(sentry_init):
    sentry_init()

    with mock.patch(
        "sentry_sdk.crons.decorator.capture_checkin"
    ) as fake_capture_checkin:
        with pytest.raises(ZeroDivisionError):
            result = await _break_world_contextmanager("Grace")

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

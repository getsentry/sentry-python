import mock

import pytest

pytest.importorskip("celery")

from sentry_sdk.integrations.celery import (
    _get_headers,
    _get_monitor_config,
    crons_task_before_run,
    crons_task_success,
    crons_task_failure,
    crons_task_retry,
)
from sentry_sdk.crons import MonitorStatus


def test_get_headers():
    fake_task = mock.MagicMock()
    fake_task.request = {
        "bla": "blub",
        "foo": "bar",
    }

    assert _get_headers(fake_task) == {}

    fake_task.request.update(
        {
            "headers": {
                "bla": "blub",
            },
        }
    )

    assert _get_headers(fake_task) == {"bla": "blub"}


def test_monitor_config():
    headers = {}
    assert _get_monitor_config(headers) == {}

    headers = {
        "bla": "blub",
        "foo": "bar",
    }
    assert _get_monitor_config(headers) == {}

    headers = {
        "bla": "blub",
        "foo": "bar",
        "sentry-monitor-schedule": [3, "day"],
        "sentry-monitor-schedule-type": "interval",
        "sentry-monitor-timezone": "Europe/Vienna",
        "sentry-monitor-some-future-key": "some-future-value",
    }
    assert _get_monitor_config(headers) == {
        "schedule": [3, "day"],
        "schedule_type": "interval",
        "timezone": "Europe/Vienna",
    }


def test_crons_task_before_run():
    fake_task = mock.MagicMock()
    fake_task.request = {
        "headers": {
            "sentry-monitor-slug": "test123",
            "sentry-monitor-schedule": [3, "day"],
            "sentry-monitor-schedule-type": "interval",
            "sentry-monitor-timezone": "Europe/Vienna",
            "sentry-monitor-some-future-key": "some-future-value",
        },
    }

    with mock.patch(
        "sentry_sdk.integrations.celery.capture_checkin"
    ) as mock_capture_checkin:
        crons_task_before_run(fake_task)

        mock_capture_checkin.assert_called_once_with(
            monitor_slug="test123",
            monitor_config={
                "schedule": [3, "day"],
                "schedule_type": "interval",
                "timezone": "Europe/Vienna",
            },
            status=MonitorStatus.IN_PROGRESS,
        )


def test_crons_task_success():
    fake_task = mock.MagicMock()
    fake_task.request = {
        "headers": {
            "sentry-monitor-slug": "test123",
            "sentry-monitor-check-in-id": "1234567890",
            "sentry-monitor-start-timestamp-s": 200.1,
            "sentry-monitor-schedule": [3, "day"],
            "sentry-monitor-schedule-type": "interval",
            "sentry-monitor-timezone": "Europe/Vienna",
            "sentry-monitor-some-future-key": "some-future-value",
        },
    }

    with mock.patch(
        "sentry_sdk.integrations.celery.capture_checkin"
    ) as mock_capture_checkin:
        with mock.patch("sentry_sdk.integrations.celery.now", return_value=500.5):
            crons_task_success(fake_task)

            mock_capture_checkin.assert_called_once_with(
                monitor_slug="test123",
                monitor_config={
                    "schedule": [3, "day"],
                    "schedule_type": "interval",
                    "timezone": "Europe/Vienna",
                },
                duration_s=300.4,
                check_in_id="1234567890",
                status=MonitorStatus.OK,
            )


def test_crons_task_failure():
    fake_task = mock.MagicMock()
    fake_task.request = {
        "headers": {
            "sentry-monitor-slug": "test123",
            "sentry-monitor-check-in-id": "1234567890",
            "sentry-monitor-start-timestamp-s": 200.1,
            "sentry-monitor-schedule": [3, "day"],
            "sentry-monitor-schedule-type": "interval",
            "sentry-monitor-timezone": "Europe/Vienna",
            "sentry-monitor-some-future-key": "some-future-value",
        },
    }

    with mock.patch(
        "sentry_sdk.integrations.celery.capture_checkin"
    ) as mock_capture_checkin:
        with mock.patch("sentry_sdk.integrations.celery.now", return_value=500.5):
            crons_task_failure(fake_task)

            mock_capture_checkin.assert_called_once_with(
                monitor_slug="test123",
                monitor_config={
                    "schedule": [3, "day"],
                    "schedule_type": "interval",
                    "timezone": "Europe/Vienna",
                },
                duration_s=300.4,
                check_in_id="1234567890",
                status=MonitorStatus.ERROR,
            )


def test_crons_task_retry():
    fake_task = mock.MagicMock()
    fake_task.request = {
        "headers": {
            "sentry-monitor-slug": "test123",
            "sentry-monitor-check-in-id": "1234567890",
            "sentry-monitor-start-timestamp-s": 200.1,
            "sentry-monitor-schedule": [3, "day"],
            "sentry-monitor-schedule-type": "interval",
            "sentry-monitor-timezone": "Europe/Vienna",
            "sentry-monitor-some-future-key": "some-future-value",
        },
    }

    with mock.patch(
        "sentry_sdk.integrations.celery.capture_checkin"
    ) as mock_capture_checkin:
        with mock.patch("sentry_sdk.integrations.celery.now", return_value=500.5):
            crons_task_retry(fake_task)

            mock_capture_checkin.assert_called_once_with(
                monitor_slug="test123",
                monitor_config={
                    "schedule": [3, "day"],
                    "schedule_type": "interval",
                    "timezone": "Europe/Vienna",
                },
                duration_s=300.4,
                check_in_id="1234567890",
                status=MonitorStatus.ERROR,
            )

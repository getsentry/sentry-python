import mock

import pytest

pytest.importorskip("celery")

from sentry_sdk.integrations.celery import (
    _get_headers,
    _get_humanized_interval,
    _get_monitor_config,
    _get_schedule_config,
    _reinstall_patched_tasks,
    crons_task_before_run,
    crons_task_success,
    crons_task_failure,
    crons_task_retry,
)
from sentry_sdk.crons import MonitorStatus
from celery.schedules import crontab, schedule


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


@pytest.mark.parametrize(
    "seconds, expected_tuple",
    [
        (0, (0, "second")),
        (0.00001, (0, "second")),
        (1, (1, "second")),
        (100, (1.67, "minute")),
        (1000, (16.67, "minute")),
        (10000, (2.78, "hour")),
        (100000, (1.16, "day")),
        (100000000, (1157.41, "day")),
    ],
)
def test_get_humanized_interval(seconds, expected_tuple):
    assert _get_humanized_interval(seconds) == expected_tuple


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
                duration=300.4,
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
                duration=300.4,
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
                duration=300.4,
                check_in_id="1234567890",
                status=MonitorStatus.ERROR,
            )


def test_get_schedule_config():
    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")

    (monitor_schedule_type, monitor_schedule) = _get_schedule_config(celery_schedule)
    assert monitor_schedule_type == "crontab"
    assert monitor_schedule == "*/10 12 3 * *"

    celery_schedule = schedule(run_every=3)

    (monitor_schedule_type, monitor_schedule) = _get_schedule_config(celery_schedule)
    assert monitor_schedule_type == "interval"
    assert monitor_schedule == (3, "second")

    unknown_celery_schedule = mock.MagicMock()
    (monitor_schedule_type, monitor_schedule) = _get_schedule_config(
        unknown_celery_schedule
    )
    assert monitor_schedule_type is None
    assert monitor_schedule is None


def test_reinstall_patched_tasks():
    fake_beat = mock.MagicMock()
    fake_beat.run = mock.MagicMock()

    app = mock.MagicMock()
    app.Beat = mock.MagicMock(return_value=fake_beat)

    sender = mock.MagicMock()
    sender.schedule_filename = "test_schedule_filename"
    sender.stop = mock.MagicMock()

    add_updated_periodic_tasks = [mock.MagicMock(), mock.MagicMock(), mock.MagicMock()]

    with mock.patch("sentry_sdk.integrations.celery.shutil.copy2") as mock_copy2:
        _reinstall_patched_tasks(app, sender, add_updated_periodic_tasks)

        sender.stop.assert_called_once_with()

        add_updated_periodic_tasks[0].assert_called_once_with()
        add_updated_periodic_tasks[1].assert_called_once_with()
        add_updated_periodic_tasks[2].assert_called_once_with()

        mock_copy2.assert_called_once_with(
            "test_schedule_filename", "test_schedule_filename.new"
        )
        fake_beat.run.assert_called_once_with()

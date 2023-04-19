import mock

import pytest

pytest.importorskip("celery")

from sentry_sdk.integrations.celery import (
    _get_headers,
    _get_humanized_interval,
    _get_monitor_config,
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

    fake_task.request.update(
        {
            "headers": {
                "headers": {
                    "tri": "blub",
                    "bar": "baz",
                },
                "bla": "blub",
            },
        }
    )

    assert _get_headers(fake_task) == {"bla": "blub", "tri": "blub", "bar": "baz"}


@pytest.mark.parametrize(
    "seconds, expected_tuple",
    [
        (0, (1, "minute")),
        (0.00001, (1, "minute")),
        (1, (1, "minute")),
        (100, (1, "minute")),
        (1000, (16, "minute")),
        (10000, (2, "hour")),
        (100000, (1, "day")),
        (100000000, (1157, "day")),
    ],
)
def test_get_humanized_interval(seconds, expected_tuple):
    assert _get_humanized_interval(seconds) == expected_tuple


def test_crons_task_success():
    fake_task = mock.MagicMock()
    fake_task.request = {
        "headers": {
            "sentry-monitor-slug": "test123",
            "sentry-monitor-check-in-id": "1234567890",
            "sentry-monitor-start-timestamp-s": 200.1,
            "sentry-monitor-config": {
                "schedule": {
                    "type": "interval",
                    "value": 3,
                    "unit": "day",
                },
                "timezone": "Europe/Vienna",
            },
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
                    "schedule": {
                        "type": "interval",
                        "value": 3,
                        "unit": "day",
                    },
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
            "sentry-monitor-config": {
                "schedule": {
                    "type": "interval",
                    "value": 3,
                    "unit": "day",
                },
                "timezone": "Europe/Vienna",
            },
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
                    "schedule": {
                        "type": "interval",
                        "value": 3,
                        "unit": "day",
                    },
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
            "sentry-monitor-config": {
                "schedule": {
                    "type": "interval",
                    "value": 3,
                    "unit": "day",
                },
                "timezone": "Europe/Vienna",
            },
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
                    "schedule": {
                        "type": "interval",
                        "value": 3,
                        "unit": "day",
                    },
                    "timezone": "Europe/Vienna",
                },
                duration=300.4,
                check_in_id="1234567890",
                status=MonitorStatus.ERROR,
            )


def test_get_monitor_config():
    app = mock.MagicMock()
    app.conf = mock.MagicMock()
    app.conf.timezone = "Europe/Vienna"

    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")

    monitor_config = _get_monitor_config(celery_schedule, app)
    assert monitor_config == {
        "schedule": {
            "type": "crontab",
            "value": "*/10 12 3 * *",
        },
        "timezone": "Europe/Vienna",
    }
    assert "unit" not in monitor_config["schedule"]

    celery_schedule = schedule(run_every=3)

    monitor_config = _get_monitor_config(celery_schedule, app)
    assert monitor_config == {
        "schedule": {
            "type": "interval",
            "value": 1,
            "unit": "minute",
        },
        "timezone": "Europe/Vienna",
    }

    unknown_celery_schedule = mock.MagicMock()
    monitor_config = _get_monitor_config(unknown_celery_schedule, app)
    assert monitor_config == {}


def test_get_monitor_config_default_timezone():
    app = mock.MagicMock()
    app.conf = mock.MagicMock()
    app.conf.timezone = None

    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")

    monitor_config = _get_monitor_config(celery_schedule, app)

    assert monitor_config["timezone"] == "UTC"

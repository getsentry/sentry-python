import datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from celery.schedules import crontab, schedule

from sentry_sdk.crons import MonitorStatus
from sentry_sdk.integrations.celery.beat import (
    _get_headers,
    _get_monitor_config,
    _patch_beat_apply_entry,
    _patch_redbeat_apply_async,
    crons_task_failure,
    crons_task_retry,
    crons_task_success,
)
from sentry_sdk.integrations.celery.utils import _get_humanized_interval


def test_get_headers():
    fake_task = MagicMock()
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
        (0, (0, "second")),
        (1, (1, "second")),
        (0.00001, (0, "second")),
        (59, (59, "second")),
        (60, (1, "minute")),
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
    fake_task = MagicMock()
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
        "sentry_sdk.integrations.celery.beat.capture_checkin"
    ) as mock_capture_checkin:
        with mock.patch(
            "sentry_sdk.integrations.celery.beat._now_seconds_since_epoch",
            return_value=500.5,
        ):
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
    fake_task = MagicMock()
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
        "sentry_sdk.integrations.celery.beat.capture_checkin"
    ) as mock_capture_checkin:
        with mock.patch(
            "sentry_sdk.integrations.celery.beat._now_seconds_since_epoch",
            return_value=500.5,
        ):
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
    fake_task = MagicMock()
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
        "sentry_sdk.integrations.celery.beat.capture_checkin"
    ) as mock_capture_checkin:
        with mock.patch(
            "sentry_sdk.integrations.celery.beat._now_seconds_since_epoch",
            return_value=500.5,
        ):
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


def test_get_monitor_config_crontab():
    app = MagicMock()
    app.timezone = "Europe/Vienna"

    # schedule with the default timezone
    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")

    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "crontab",
            "value": "*/10 12 3 * *",
        },
        "timezone": "UTC",  # the default because `crontab` does not know about the app
    }
    assert "unit" not in monitor_config["schedule"]

    # schedule with the timezone from the app
    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10", app=app)

    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "crontab",
            "value": "*/10 12 3 * *",
        },
        "timezone": "Europe/Vienna",  # the timezone from the app
    }

    # schedule without a timezone, the celery integration will read the config from the app
    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")
    celery_schedule.tz = None

    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "crontab",
            "value": "*/10 12 3 * *",
        },
        "timezone": "Europe/Vienna",  # the timezone from the app
    }

    # schedule without a timezone, and an app without timezone, the celery integration will fall back to UTC
    app = MagicMock()
    app.timezone = None

    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")
    celery_schedule.tz = None
    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "crontab",
            "value": "*/10 12 3 * *",
        },
        "timezone": "UTC",  # default timezone from celery integration
    }


def test_get_monitor_config_seconds():
    app = MagicMock()
    app.timezone = "Europe/Vienna"

    celery_schedule = schedule(run_every=3)  # seconds

    with mock.patch("sentry_sdk.integrations.logger.warning") as mock_logger_warning:
        monitor_config = _get_monitor_config(celery_schedule, app, "foo")
        mock_logger_warning.assert_called_with(
            "Intervals shorter than one minute are not supported by Sentry Crons. Monitor '%s' has an interval of %s seconds. Use the `exclude_beat_tasks` option in the celery integration to exclude it.",
            "foo",
            3,
        )
        assert monitor_config == {}


def test_get_monitor_config_minutes():
    app = MagicMock()
    app.timezone = "Europe/Vienna"

    # schedule with the default timezone
    celery_schedule = schedule(run_every=60)  # seconds

    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "interval",
            "value": 1,
            "unit": "minute",
        },
        "timezone": "UTC",
    }

    # schedule with the timezone from the app
    celery_schedule = schedule(run_every=60, app=app)  # seconds

    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "interval",
            "value": 1,
            "unit": "minute",
        },
        "timezone": "Europe/Vienna",  # the timezone from the app
    }

    # schedule without a timezone, the celery integration will read the config from the app
    celery_schedule = schedule(run_every=60)  # seconds
    celery_schedule.tz = None

    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "interval",
            "value": 1,
            "unit": "minute",
        },
        "timezone": "Europe/Vienna",  # the timezone from the app
    }

    # schedule without a timezone, and an app without timezone, the celery integration will fall back to UTC
    app = MagicMock()
    app.timezone = None

    celery_schedule = schedule(run_every=60)  # seconds
    celery_schedule.tz = None

    monitor_config = _get_monitor_config(celery_schedule, app, "foo")
    assert monitor_config == {
        "schedule": {
            "type": "interval",
            "value": 1,
            "unit": "minute",
        },
        "timezone": "UTC",  # default timezone from celery integration
    }


def test_get_monitor_config_unknown():
    app = MagicMock()
    app.timezone = "Europe/Vienna"

    unknown_celery_schedule = MagicMock()
    monitor_config = _get_monitor_config(unknown_celery_schedule, app, "foo")
    assert monitor_config == {}


def test_get_monitor_config_default_timezone():
    app = MagicMock()
    app.timezone = None

    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")

    monitor_config = _get_monitor_config(celery_schedule, app, "dummy_monitor_name")

    assert monitor_config["timezone"] == "UTC"


def test_get_monitor_config_timezone_in_app_conf():
    app = MagicMock()
    app.timezone = "Asia/Karachi"

    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")
    celery_schedule.tz = None

    monitor_config = _get_monitor_config(celery_schedule, app, "dummy_monitor_name")

    assert monitor_config["timezone"] == "Asia/Karachi"


def test_get_monitor_config_timezone_in_celery_schedule():
    app = MagicMock()
    app.timezone = "Asia/Karachi"

    panama_tz = datetime.timezone(datetime.timedelta(hours=-5), name="America/Panama")

    celery_schedule = crontab(day_of_month="3", hour="12", minute="*/10")
    celery_schedule.tz = panama_tz

    monitor_config = _get_monitor_config(celery_schedule, app, "dummy_monitor_name")

    assert monitor_config["timezone"] == str(panama_tz)


@pytest.mark.parametrize(
    "task_name,exclude_beat_tasks,task_in_excluded_beat_tasks",
    [
        ["some_task_name", ["xxx", "some_task.*"], True],
        ["some_task_name", ["xxx", "some_other_task.*"], False],
    ],
)
def test_exclude_beat_tasks_option(
    task_name, exclude_beat_tasks, task_in_excluded_beat_tasks
):
    """
    Test excluding Celery Beat tasks from automatic instrumentation.
    """
    fake_apply_entry = MagicMock()

    fake_scheduler = MagicMock()
    fake_scheduler.apply_entry = fake_apply_entry

    fake_integration = MagicMock()
    fake_integration.exclude_beat_tasks = exclude_beat_tasks

    fake_client = MagicMock()
    fake_client.get_integration.return_value = fake_integration

    fake_schedule_entry = MagicMock()
    fake_schedule_entry.name = task_name

    fake_get_monitor_config = MagicMock()

    with mock.patch(
        "sentry_sdk.integrations.celery.beat.Scheduler", fake_scheduler
    ) as Scheduler:  # noqa: N806
        with mock.patch(
            "sentry_sdk.integrations.celery.sentry_sdk.get_client",
            return_value=fake_client,
        ):
            with mock.patch(
                "sentry_sdk.integrations.celery.beat._get_monitor_config",
                fake_get_monitor_config,
            ) as _get_monitor_config:
                # Mimic CeleryIntegration patching of Scheduler.apply_entry()
                _patch_beat_apply_entry()
                # Mimic Celery Beat calling a task from the Beat schedule
                Scheduler.apply_entry(fake_scheduler, fake_schedule_entry)

                if task_in_excluded_beat_tasks:
                    # Only the original Scheduler.apply_entry() is called, _get_monitor_config is NOT called.
                    assert fake_apply_entry.call_count == 1
                    _get_monitor_config.assert_not_called()

                else:
                    # The original Scheduler.apply_entry() is called, AND _get_monitor_config is called.
                    assert fake_apply_entry.call_count == 1
                    assert _get_monitor_config.call_count == 1


@pytest.mark.parametrize(
    "task_name,exclude_beat_tasks,task_in_excluded_beat_tasks",
    [
        ["some_task_name", ["xxx", "some_task.*"], True],
        ["some_task_name", ["xxx", "some_other_task.*"], False],
    ],
)
def test_exclude_redbeat_tasks_option(
    task_name, exclude_beat_tasks, task_in_excluded_beat_tasks
):
    """
    Test excluding Celery RedBeat tasks from automatic instrumentation.
    """
    fake_apply_async = MagicMock()

    fake_redbeat_scheduler = MagicMock()
    fake_redbeat_scheduler.apply_async = fake_apply_async

    fake_integration = MagicMock()
    fake_integration.exclude_beat_tasks = exclude_beat_tasks

    fake_client = MagicMock()
    fake_client.get_integration.return_value = fake_integration

    fake_schedule_entry = MagicMock()
    fake_schedule_entry.name = task_name

    fake_get_monitor_config = MagicMock()

    with mock.patch(
        "sentry_sdk.integrations.celery.beat.RedBeatScheduler", fake_redbeat_scheduler
    ) as RedBeatScheduler:  # noqa: N806
        with mock.patch(
            "sentry_sdk.integrations.celery.sentry_sdk.get_client",
            return_value=fake_client,
        ):
            with mock.patch(
                "sentry_sdk.integrations.celery.beat._get_monitor_config",
                fake_get_monitor_config,
            ) as _get_monitor_config:
                # Mimic CeleryIntegration patching of RedBeatScheduler.apply_async()
                _patch_redbeat_apply_async()
                # Mimic Celery RedBeat calling a task from the RedBeat schedule
                RedBeatScheduler.apply_async(
                    fake_redbeat_scheduler, fake_schedule_entry
                )

                if task_in_excluded_beat_tasks:
                    # Only the original RedBeatScheduler.maybe_due() is called, _get_monitor_config is NOT called.
                    assert fake_apply_async.call_count == 1
                    _get_monitor_config.assert_not_called()

                else:
                    # The original RedBeatScheduler.maybe_due() is called, AND _get_monitor_config is called.
                    assert fake_apply_async.call_count == 1
                    assert _get_monitor_config.call_count == 1

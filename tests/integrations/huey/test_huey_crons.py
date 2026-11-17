from datetime import datetime

import pytest
from huey import crontab
from huey.api import MemoryHuey
from huey.exceptions import CancelExecution, RetryTask, TaskLockedException

from sentry_sdk.integrations.huey import HueyIntegration, _get_monitor_config


def _check_ins(envelopes):
    return [
        item.payload.json
        for envelope in envelopes
        for item in envelope.items
        if item.headers.get("type") == "check_in"
    ]


def _run_periodic_tasks(huey, timestamp=datetime(2026, 1, 1)):
    """Run the periodic tasks due at `timestamp` the way the consumer does."""
    for task in huey.read_periodic(timestamp):
        huey.enqueue(task)
    while True:
        task = huey.dequeue()
        if task is None:
            break
        huey.execute(task)


def test_periodic_task_ok(sentry_init, capture_envelopes):
    sentry_init(integrations=[HueyIntegration(monitor_periodic_tasks=True)])
    envelopes = capture_envelopes()
    huey = MemoryHuey()

    @huey.periodic_task(crontab(minute="*/5"))
    def cleanup():
        pass

    _run_periodic_tasks(huey)

    in_progress, ok = _check_ins(envelopes)
    assert in_progress["monitor_slug"] == "cleanup"
    assert in_progress["status"] == "in_progress"
    assert in_progress["monitor_config"] == {
        "schedule": {"type": "crontab", "value": "*/5 * * * *"},
        "timezone": "UTC",
    }
    assert ok["monitor_slug"] == "cleanup"
    assert ok["status"] == "ok"
    assert ok["check_in_id"] == in_progress["check_in_id"]
    assert ok["duration"] >= 0


def test_periodic_task_error(sentry_init, capture_envelopes):
    sentry_init(integrations=[HueyIntegration(monitor_periodic_tasks=True)])
    envelopes = capture_envelopes()
    huey = MemoryHuey()

    @huey.periodic_task(crontab(minute="0", hour="0"), name="nightly")
    def failing():
        raise ValueError("oops")

    _run_periodic_tasks(huey)

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "error"]
    assert {c["monitor_slug"] for c in check_ins} == {"nightly"}

    events = [envelope.get_event() for envelope in envelopes]
    (error,) = [e for e in events if e is not None and e.get("exception")]
    assert error["exception"]["values"][0]["type"] == "ValueError"


@pytest.mark.parametrize("exception", [CancelExecution, RetryTask, TaskLockedException])
def test_periodic_task_control_flow(sentry_init, capture_envelopes, exception):
    sentry_init(integrations=[HueyIntegration(monitor_periodic_tasks=True)])
    envelopes = capture_envelopes()
    huey = MemoryHuey()

    calls = []

    @huey.periodic_task(crontab())
    def cleanup():
        if not calls:
            calls.append(1)
            raise exception()

    _run_periodic_tasks(huey)

    statuses = [c["status"] for c in _check_ins(envelopes)]
    assert statuses[:2] == ["in_progress", "error"]


def test_regular_tasks_not_monitored(sentry_init, capture_envelopes):
    sentry_init(integrations=[HueyIntegration(monitor_periodic_tasks=True)])
    envelopes = capture_envelopes()
    huey = MemoryHuey()

    @huey.task()
    def add(a, b):
        return a + b

    add(1, 2)
    huey.execute(huey.dequeue())

    assert _check_ins(envelopes) == []


def test_periodic_tasks_not_monitored_by_default(sentry_init, capture_envelopes):
    sentry_init(integrations=[HueyIntegration()])
    envelopes = capture_envelopes()
    huey = MemoryHuey()

    @huey.periodic_task(crontab())
    def cleanup():
        pass

    _run_periodic_tasks(huey)

    assert _check_ins(envelopes) == []


def test_exclude_periodic_tasks(sentry_init, capture_envelopes):
    sentry_init(
        integrations=[
            HueyIntegration(
                monitor_periodic_tasks=True, exclude_periodic_tasks=["clean.*"]
            )
        ]
    )
    envelopes = capture_envelopes()
    huey = MemoryHuey()

    @huey.periodic_task(crontab())
    def cleanup():
        pass

    @huey.periodic_task(crontab())
    def report():
        pass

    _run_periodic_tasks(huey)

    assert {c["monitor_slug"] for c in _check_ins(envelopes)} == {"report"}


def _every_minute(timestamp):
    return True


@pytest.mark.parametrize(
    "utc, validate_datetime",
    [
        (True, _every_minute),
        (True, crontab(minute="0", hour="0", day="1", day_of_week="1")),
        (False, crontab()),
    ],
)
def test_unsupported_schedule_not_monitored(
    sentry_init, capture_envelopes, utc, validate_datetime
):
    sentry_init(integrations=[HueyIntegration(monitor_periodic_tasks=True)])
    envelopes = capture_envelopes()
    huey = MemoryHuey(utc=utc)
    calls = []

    @huey.periodic_task(validate_datetime)
    def cleanup():
        calls.append(1)

    _run_periodic_tasks(huey, timestamp=datetime(2026, 6, 1))

    assert calls == [1]
    assert _check_ins(envelopes) == []


def _config(huey, validate_datetime):
    @huey.periodic_task(validate_datetime)
    def task():
        pass

    return _get_monitor_config(huey, task.task_class())


@pytest.mark.parametrize(
    "kwargs, value",
    [
        ({}, "* * * * *"),
        ({"minute": "0"}, "0 * * * *"),
        ({"minute": 30, "hour": "9"}, "30 9 * * *"),
        ({"minute": "*/15"}, "*/15 * * * *"),
        ({"minute": "0", "hour": "*/6"}, "0 */6 * * *"),
        ({"minute": "0", "hour": "9-17"}, "0 9-17 * * *"),
        ({"minute": "0", "hour": "9,17"}, "0 9,17 * * *"),
        ({"minute": "0", "hour": "8,12,16,20"}, "0 8-20/4 * * *"),
        ({"minute": "0", "hour": "1,2,5"}, "0 1,2,5 * * *"),
        ({"minute": "0", "hour": "0", "day": "1"}, "0 0 1 * *"),
        ({"minute": "0", "hour": "0", "day": "1-31"}, "0 0 * * *"),
        ({"minute": "0", "hour": "0", "day": "1", "month": "*/3"}, "0 0 1 */3 *"),
        ({"minute": "0", "hour": "0", "day_of_week": "1-5"}, "0 0 * * 1-5"),
        ({"minute": "0", "hour": "0", "day_of_week": "0,6"}, "0 0 * * 0,6"),
    ],
)
def test_get_monitor_config(kwargs, value):
    assert _config(MemoryHuey(), crontab(**kwargs)) == {
        "schedule": {"type": "crontab", "value": value},
        "timezone": "UTC",
    }


@pytest.mark.skipif(
    not hasattr(crontab, "hourly"), reason="crontab.hourly not available"
)
def test_get_monitor_config_hourly():
    assert _config(MemoryHuey(), crontab.hourly())["schedule"]["value"] == ("0 * * * *")


def test_get_monitor_config_day_and_weekday():
    # Huey requires both to match, cron either.
    assert _config(MemoryHuey(), crontab(day="1", day_of_week="1")) is None


def test_get_monitor_config_local_time():
    assert _config(MemoryHuey(utc=False), crontab()) is None

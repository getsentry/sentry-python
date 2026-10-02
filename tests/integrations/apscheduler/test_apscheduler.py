import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.executors.pool import ProcessPoolExecutor, ThreadPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from sentry_sdk.integrations.apscheduler import APSchedulerIntegration


def _utcnow():
    # Naive UTC; the schedulers below run in UTC. APScheduler < 3.7 only takes
    # pytz timezones, so avoid passing tzinfo objects.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _check_ins(envelopes):
    return [
        item.payload.json
        for envelope in envelopes
        for item in envelope.items
        if item.headers.get("type") == "check_in"
    ]


def ok_job():
    pass


def failing_job():
    raise ValueError("oops")


def _run_once(envelopes, trigger, func=ok_job, executor="debug", **job_kwargs):
    """Run a job once through a real scheduler and return the check-ins."""
    finished = threading.Event()

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_executor(executor, alias="default")
    # Added before the integration's listener, which is registered on the
    # first submitted job, but both run in the same thread for each event.
    scheduler.add_listener(
        lambda event: finished.set(),
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )
    job_kwargs.setdefault("id", "my-job")
    job_kwargs.setdefault("next_run_time", _utcnow())
    scheduler.add_job(func, trigger, **job_kwargs)
    scheduler.start()
    try:
        assert finished.wait(timeout=10)
    finally:
        # Waits for the executor, so for the integration's listener, too.
        scheduler.shutdown(wait=True)

    return _check_ins(envelopes)


def test_cron_job_ok(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(minute="*/5", timezone="UTC"))

    assert len(check_ins) == 2
    in_progress, ok = check_ins

    assert in_progress["monitor_slug"] == "my-job"
    assert in_progress["status"] == "in_progress"
    assert in_progress["monitor_config"] == {
        "schedule": {"type": "crontab", "value": "*/5 * * * *"},
        "timezone": "UTC",
    }

    assert ok["monitor_slug"] == "my-job"
    assert ok["status"] == "ok"
    assert ok["check_in_id"] == in_progress["check_in_id"]
    assert ok["monitor_config"] == in_progress["monitor_config"]
    assert ok["duration"] >= 0


def test_cron_job_error(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes, CronTrigger(minute="*/5", timezone="UTC"), failing_job
    )

    assert [c["status"] for c in check_ins] == ["in_progress", "error"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


def test_missed_run_is_error(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes,
        CronTrigger(minute="*/5", timezone="UTC"),
        next_run_time=_utcnow() - timedelta(minutes=1),
        misfire_grace_time=1,
    )

    assert [c["status"] for c in check_ins] == ["in_progress", "error"]


def test_monitoring_is_opt_in(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(minute="*/5", timezone="UTC"))

    assert check_ins == []


def test_exclude_jobs(sentry_init, capture_envelopes):
    sentry_init(
        integrations=[APSchedulerIntegration(monitor_jobs=True, exclude_jobs=["my-.*"])]
    )
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(minute="*/5", timezone="UTC"))

    assert check_ins == []


def test_generated_job_id_uses_job_name(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(minute="*/5", timezone="UTC"), id=None)

    assert [c["monitor_slug"] for c in check_ins] == ["ok_job", "ok_job"]


@pytest.mark.parametrize(
    "cron_kwargs, expected",
    [
        ({"minute": "*/5"}, "*/5 * * * *"),
        ({"hour": 3}, "0 3 * * *"),
        ({"hour": "9-17", "minute": "30"}, "30 9-17 * * *"),
        ({"day": 1, "hour": 0}, "0 0 1 * *"),
        ({"month": "1-3", "day": 15}, "0 0 15 1-3 *"),
        ({"day_of_week": "mon-fri", "hour": 9}, "0 9 * * 1-5"),
        ({"day_of_week": "sat,sun", "hour": 9}, "0 9 * * 6,7"),
        ({"day_of_week": "0", "hour": 9}, "0 9 * * 1"),
        ({"day_of_week": "*/2", "hour": 9}, "0 9 * * 1-7/2"),
    ],
)
def test_cron_trigger_monitor_config(
    sentry_init, capture_envelopes, cron_kwargs, expected
):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(timezone="UTC", **cron_kwargs))

    assert check_ins[0]["monitor_config"]["schedule"] == {
        "type": "crontab",
        "value": expected,
    }


@pytest.mark.parametrize(
    "cron_kwargs",
    [
        # Seconds and years have no 5-field crontab equivalent.
        {"second": "*/30"},
        {"year": 2030},
        {"week": 2},
        # APScheduler ANDs day and day_of_week, cron ORs them.
        {"day": 1, "day_of_week": "mon"},
        {"day": "last"},
        {"day": "1st mon"},
    ],
)
def test_cron_trigger_without_crontab_equivalent(
    sentry_init, capture_envelopes, cron_kwargs
):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(timezone="UTC", **cron_kwargs))

    assert check_ins == []


def test_cron_trigger_timezone(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(hour=9, timezone="Europe/Vienna"))

    assert check_ins[0]["monitor_config"]["timezone"] == "Europe/Vienna"


@pytest.mark.parametrize(
    "interval_kwargs, expected",
    [
        ({"minutes": 1}, (1, "minute")),
        ({"minutes": 90}, (90, "minute")),
        ({"hours": 2}, (2, "hour")),
        ({"days": 1}, (1, "day")),
        ({"weeks": 1}, (7, "day")),
    ],
)
def test_interval_trigger_monitor_config(
    sentry_init, capture_envelopes, interval_kwargs, expected
):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, IntervalTrigger(timezone="UTC", **interval_kwargs))

    value, unit = expected
    assert check_ins[0]["monitor_config"] == {
        "schedule": {"type": "interval", "value": value, "unit": unit},
    }
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]


@pytest.mark.parametrize("interval_kwargs", [{"seconds": 30}, {"seconds": 90}])
def test_interval_trigger_not_whole_minutes(
    sentry_init, capture_envelopes, interval_kwargs
):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, IntervalTrigger(timezone="UTC", **interval_kwargs))

    assert check_ins == []


def test_date_trigger_is_not_monitored(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, DateTrigger(run_date=_utcnow(), timezone="UTC"))

    assert check_ins == []


def test_thread_pool_executor(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes,
        CronTrigger(minute="*/5", timezone="UTC"),
        executor=ThreadPoolExecutor(),
    )

    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


def test_process_pool_executor(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes,
        CronTrigger(minute="*/5", timezone="UTC"),
        failing_job,
        executor=ProcessPoolExecutor(1),
    )

    assert [c["status"] for c in check_ins] == ["in_progress", "error"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


async def async_ok_job():
    await asyncio.sleep(0)


def test_asyncio_scheduler(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(monitor_jobs=True)])
    envelopes = capture_envelopes()

    async def main():
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            async_ok_job,
            CronTrigger(minute="*/5", timezone="UTC"),
            id="my-async-job",
            next_run_time=_utcnow(),
        )
        scheduler.start()
        try:
            for _ in range(500):
                if len(_check_ins(envelopes)) >= 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            scheduler.shutdown()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]
    assert [c["monitor_slug"] for c in check_ins] == ["my-async-job"] * 2

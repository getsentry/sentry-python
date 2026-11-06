import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
)
from apscheduler.executors.pool import ProcessPoolExecutor, ThreadPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

import sentry_sdk
from sentry_sdk.integrations import apscheduler as apscheduler_integration
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
    job_kwargs.setdefault("misfire_grace_time", None)
    scheduler.add_job(func, trigger, **job_kwargs)
    scheduler.start()
    try:
        assert finished.wait(timeout=10)
    finally:
        # Waits for the executor, so for the integration's listener, too.
        scheduler.shutdown(wait=True)

    return _check_ins(envelopes)


def test_cron_job_ok(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
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
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes, CronTrigger(minute="*/5", timezone="UTC"), failing_job
    )

    assert [c["status"] for c in check_ins] == ["in_progress", "error"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


@pytest.mark.parametrize("executor", ["debug", "threadpool"])
def test_missed_run_sends_no_check_in(sentry_init, capture_envelopes, executor):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    # A cron slot could fall due during the test, and coalescing would run it.
    check_ins = _run_once(
        envelopes,
        IntervalTrigger(hours=1, timezone="UTC"),
        executor=executor,
        next_run_time=_utcnow() - timedelta(minutes=1),
        misfire_grace_time=1,
    )

    assert check_ins == []


def test_run_missed_after_in_progress_is_error(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    started = threading.Event()
    release = threading.Event()
    missed = threading.Event()

    def blocking_job():
        started.set()
        assert release.wait(timeout=10)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_executor(ThreadPoolExecutor(1), alias="default")
    scheduler.add_listener(lambda event: missed.set(), EVENT_JOB_MISSED)
    scheduler.add_job(
        blocking_job,
        CronTrigger(minute="*/5", timezone="UTC"),
        id="blocker",
        next_run_time=_utcnow(),
        misfire_grace_time=None,
    )
    scheduler.start()
    try:
        assert started.wait(timeout=10)
        # Waits behind the blocker in the only thread until it's past its grace time.
        scheduler.add_job(
            ok_job,
            CronTrigger(minute="*/5", timezone="UTC"),
            id="my-job",
            next_run_time=_utcnow(),
            misfire_grace_time=1,
        )
        time.sleep(1.5)
        release.set()
        assert missed.wait(timeout=10)
    finally:
        release.set()
        scheduler.shutdown(wait=True)

    check_ins = [c for c in _check_ins(envelopes) if c["monitor_slug"] == "my-job"]
    assert [c["status"] for c in check_ins] == ["in_progress", "error"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


def test_backlog_checks_in_once(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    # Three run times are due, and without coalescing they run in one submit.
    check_ins = _run_once(
        envelopes,
        IntervalTrigger(minutes=1, timezone="UTC"),
        next_run_time=_utcnow() - timedelta(minutes=2, seconds=30),
        coalesce=False,
    )

    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


def test_without_integration_sends_no_check_ins(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(minute="*/5", timezone="UTC"))

    assert check_ins == []


@pytest.mark.tests_internal_exceptions
@pytest.mark.parametrize("executor", ["debug", "threadpool"])
def test_check_in_error_does_not_break_job(
    sentry_init, capture_envelopes, monkeypatch, executor
):
    calls = []

    def broken_capture_checkin(*args, **kwargs):
        calls.append(kwargs["status"])
        raise RuntimeError("broken")

    monkeypatch.setattr(
        apscheduler_integration, "capture_checkin", broken_capture_checkin
    )
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    ran = threading.Event()
    executed = []

    def job():
        ran.set()

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_executor(executor, alias="default")
    scheduler.add_listener(lambda event: executed.append(event), EVENT_JOB_EXECUTED)
    scheduler.add_job(
        job,
        CronTrigger(minute="*/5", timezone="UTC"),
        id="my-job",
        next_run_time=_utcnow(),
        misfire_grace_time=None,
    )
    scheduler.start()
    try:
        assert ran.wait(timeout=10)
    finally:
        scheduler.shutdown(wait=True)

    assert len(executed) == 1
    assert calls
    assert _check_ins(envelopes) == []


def test_modified_trigger_updates_monitor_config(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    finished = threading.Event()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_executor("debug", alias="default")
    scheduler.add_listener(lambda event: finished.set(), EVENT_JOB_EXECUTED)
    scheduler.add_job(
        ok_job,
        CronTrigger(minute="*/5", timezone="UTC"),
        id="my-job",
        next_run_time=_utcnow(),
        misfire_grace_time=None,
    )
    scheduler.start()
    try:
        assert finished.wait(timeout=10)
        finished.clear()
        scheduler.modify_job(
            "my-job",
            trigger=IntervalTrigger(hours=2, timezone="UTC"),
            next_run_time=_utcnow(),
        )
        assert finished.wait(timeout=10)
    finally:
        scheduler.shutdown(wait=True)

    crons_listeners = [
        listener
        for listener, _ in scheduler._listeners
        if isinstance(listener, apscheduler_integration._CronsListener)
    ]
    assert len(crons_listeners) == 1

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"] * 2
    assert [c["monitor_config"]["schedule"] for c in check_ins[::2]] == [
        {"type": "crontab", "value": "*/5 * * * *"},
        {"type": "interval", "value": 2, "unit": "hour"},
    ]
    assert check_ins[0]["check_in_id"] != check_ins[2]["check_in_id"]


def test_multiple_schedulers(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    finished = threading.Semaphore(0)
    schedulers = []
    for _ in range(2):
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_executor(ThreadPoolExecutor(), alias="default")
        scheduler.add_listener(lambda event: finished.release(), EVENT_JOB_EXECUTED)
        scheduler.add_job(
            ok_job,
            CronTrigger(minute="*/5", timezone="UTC"),
            id="my-job",
            next_run_time=_utcnow(),
            misfire_grace_time=None,
        )
        schedulers.append(scheduler)

    for scheduler in schedulers:
        scheduler.start()
    try:
        for _ in schedulers:
            assert finished.acquire(timeout=10)
    finally:
        for scheduler in schedulers:
            scheduler.shutdown(wait=True)

    check_ins = _check_ins(envelopes)
    assert sorted(c["status"] for c in check_ins) == ["in_progress"] * 2 + ["ok"] * 2
    in_progress_ids = {
        c["check_in_id"] for c in check_ins if c["status"] == "in_progress"
    }
    ok_ids = {c["check_in_id"] for c in check_ins if c["status"] == "ok"}
    assert len(in_progress_ids) == 2
    assert ok_ids == in_progress_ids


def test_exclude_cron_jobs(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration(exclude_cron_jobs=["my-.*"])])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(minute="*/5", timezone="UTC"))

    assert check_ins == []


@sentry_sdk.monitor(monitor_slug="decorated")
def decorated_job():
    pass


def test_monitor_decorated_job_reports_once(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes, CronTrigger(minute="*/5", timezone="UTC"), decorated_job
    )

    assert [(c["monitor_slug"], c["status"]) for c in check_ins] == [
        ("decorated", "in_progress"),
        ("decorated", "ok"),
    ]


class DecoratedJobs:
    @sentry_sdk.monitor(monitor_slug="decorated-method")
    def run(self):
        pass


def test_monitor_decorated_method_reports_once(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes, CronTrigger(minute="*/5", timezone="UTC"), DecoratedJobs().run
    )

    assert [(c["monitor_slug"], c["status"]) for c in check_ins] == [
        ("decorated-method", "in_progress"),
        ("decorated-method", "ok"),
    ]


def test_generated_job_id_uses_job_name(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
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
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(timezone="UTC", **cron_kwargs))

    assert check_ins[0]["monitor_config"]["schedule"] == {
        "type": "crontab",
        "value": expected,
    }


def test_cron_trigger_timezone(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, CronTrigger(hour=9, timezone="Europe/Vienna"))

    assert check_ins[0]["monitor_config"]["timezone"] == "Europe/Vienna"


def test_cron_trigger_pytz_timezone(sentry_init, capture_envelopes):
    pytz = pytest.importorskip("pytz")
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes, CronTrigger(hour=9, timezone=pytz.timezone("Europe/Vienna"))
    )

    assert check_ins[0]["monitor_config"] == {
        "schedule": {"type": "crontab", "value": "0 9 * * *"},
        "timezone": "Europe/Vienna",
    }


@pytest.mark.parametrize(
    "offset, expected",
    [
        (timedelta(hours=2), "Etc/GMT-2"),
        (timedelta(hours=-7), "Etc/GMT+7"),
        (timedelta(0), "UTC"),
    ],
)
def test_cron_trigger_fixed_offset_timezone(
    sentry_init, capture_envelopes, offset, expected
):
    try:
        trigger = CronTrigger(hour=9, timezone=timezone(offset))
    except TypeError:
        pytest.skip("APScheduler < 3.7 only takes pytz timezones.")

    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, trigger)

    assert check_ins[0]["monitor_config"]["timezone"] == expected


def _non_whole_hour_cron_trigger():
    try:
        return CronTrigger(hour=9, timezone=timezone(timedelta(hours=5, minutes=30)))
    except TypeError:
        # APScheduler < 3.7 only takes pytz timezones.
        import pytz

        return CronTrigger(hour=9, timezone=pytz.FixedOffset(330))


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
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, IntervalTrigger(timezone="UTC", **interval_kwargs))

    value, unit = expected
    assert check_ins[0]["monitor_config"] == {
        "schedule": {"type": "interval", "value": value, "unit": unit},
    }
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]


@pytest.mark.parametrize(
    "trigger_cls,trigger_kwargs",
    [(CronTrigger, {"minute": "*/5"}), (IntervalTrigger, {"minutes": 5})],
)
@pytest.mark.parametrize("jitter, checkin_margin", [(1, 2), (60, 2), (61, 3), (300, 6)])
def test_trigger_jitter_adds_checkin_margin(
    sentry_init, capture_envelopes, trigger_cls, trigger_kwargs, jitter, checkin_margin
):
    try:
        trigger = trigger_cls(timezone="UTC", jitter=jitter, **trigger_kwargs)
    except TypeError:
        pytest.skip("APScheduler < 3.5 has no jitter.")

    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, trigger)

    assert check_ins[0]["monitor_config"]["checkin_margin"] == checkin_margin


@pytest.mark.parametrize(
    "trigger_cls,trigger_kwargs",
    [(CronTrigger, {"minute": "*/5"}), (IntervalTrigger, {"minutes": 5})],
)
def test_trigger_without_jitter_has_no_checkin_margin(
    sentry_init, capture_envelopes, trigger_cls, trigger_kwargs
):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, trigger_cls(timezone="UTC", **trigger_kwargs))

    assert "checkin_margin" not in check_ins[0]["monitor_config"]


def _combining_trigger(name):
    combining = pytest.importorskip("apscheduler.triggers.combining")
    # AndTrigger never finds a next fire time for crons that disagree.
    return getattr(combining, name)(
        [
            CronTrigger(minute="*/5", timezone="UTC"),
            CronTrigger(minute="*/5", timezone="UTC"),
        ]
    )


def _calendar_interval_trigger():
    calendarinterval = pytest.importorskip("apscheduler.triggers.calendarinterval")
    return calendarinterval.CalendarIntervalTrigger(days=1, timezone="UTC")


def _tomorrow():
    return _utcnow() + timedelta(days=1)


@pytest.mark.parametrize(
    "make_trigger",
    [
        pytest.param(lambda: CronTrigger(second="*/30", timezone="UTC"), id="second"),
        pytest.param(lambda: CronTrigger(year=2030, timezone="UTC"), id="year"),
        pytest.param(lambda: CronTrigger(week=2, timezone="UTC"), id="week"),
        # APScheduler ANDs day and day_of_week, cron ORs them.
        pytest.param(
            lambda: CronTrigger(day=1, day_of_week="mon", timezone="UTC"),
            id="day-and-day-of-week",
        ),
        pytest.param(lambda: CronTrigger(day="last", timezone="UTC"), id="last-day"),
        pytest.param(lambda: CronTrigger(day="1st mon", timezone="UTC"), id="nth-day"),
        # Sentry rejects timezones like "UTC+05:30", and without one it would
        # read the crontab as UTC.
        pytest.param(_non_whole_hour_cron_trigger, id="non-iana-timezone"),
        pytest.param(lambda: IntervalTrigger(seconds=30, timezone="UTC"), id="30s"),
        pytest.param(lambda: IntervalTrigger(seconds=90, timezone="UTC"), id="90s"),
        pytest.param(
            lambda: DateTrigger(run_date=_utcnow(), timezone="UTC"), id="date"
        ),
        pytest.param(lambda: _combining_trigger("OrTrigger"), id="or"),
        pytest.param(lambda: _combining_trigger("AndTrigger"), id="and"),
        pytest.param(_calendar_interval_trigger, id="calendar-interval"),
        pytest.param(
            lambda: CronTrigger(minute="*/5", timezone="UTC", end_date=_tomorrow()),
            id="cron-end-date",
        ),
        pytest.param(
            lambda: IntervalTrigger(minutes=5, timezone="UTC", end_date=_tomorrow()),
            id="interval-end-date",
        ),
    ],
)
def test_trigger_not_monitored(sentry_init, capture_envelopes, make_trigger):
    trigger = make_trigger()
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(envelopes, trigger)

    assert check_ins == []


def test_thread_pool_executor(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    check_ins = _run_once(
        envelopes,
        CronTrigger(minute="*/5", timezone="UTC"),
        executor=ThreadPoolExecutor(),
    )

    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


def test_max_instances_skip_sends_no_check_in(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    started = threading.Event()
    release = threading.Event()
    skipped = threading.Event()
    finished = threading.Event()

    def blocking_job():
        started.set()
        assert release.wait(timeout=10)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_executor(ThreadPoolExecutor(), alias="default")
    scheduler.add_listener(lambda event: skipped.set(), EVENT_JOB_MAX_INSTANCES)
    scheduler.add_listener(lambda event: finished.set(), EVENT_JOB_EXECUTED)
    scheduler.add_job(
        blocking_job,
        CronTrigger(minute="*/5", timezone="UTC"),
        id="my-job",
        next_run_time=_utcnow(),
        misfire_grace_time=None,
        max_instances=1,
    )
    scheduler.start()
    try:
        assert started.wait(timeout=10)
        # Due again while the first run is still going.
        scheduler.modify_job("my-job", next_run_time=_utcnow())
        assert skipped.wait(timeout=10)
        release.set()
        assert finished.wait(timeout=10)
    finally:
        release.set()
        scheduler.shutdown(wait=True)

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


def test_process_pool_executor(sentry_init, capture_envelopes):
    sentry_init(integrations=[APSchedulerIntegration()])
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
    sentry_init(integrations=[APSchedulerIntegration()])
    envelopes = capture_envelopes()

    async def main():
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            async_ok_job,
            CronTrigger(minute="*/5", timezone="UTC"),
            id="my-async-job",
            next_run_time=_utcnow(),
            misfire_grace_time=None,
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

import time
from datetime import datetime, timedelta, timezone

import pytest
import rq
from fakeredis import FakeStrictRedis

from sentry_sdk.integrations.rq import RqIntegration, _get_crontab_config

rq_scheduler = pytest.importorskip("rq_scheduler")


def _set_local_timezone(monkeypatch, tz):
    monkeypatch.setenv("TZ", tz)
    time.tzset()


@pytest.fixture(autouse=True)
def utc_local_time(monkeypatch):
    _set_local_timezone(monkeypatch, "UTC")
    yield
    monkeypatch.undo()
    time.tzset()


def ok_job():
    pass


def failing_job():
    raise ValueError("oops")


def _check_ins(envelopes):
    return [
        item.payload.json
        for envelope in envelopes
        for item in envelope.items
        if item.headers.get("type") == "check_in"
    ]


def _run(schedule):
    """Schedule jobs with rq-scheduler, push them to the queue and run them."""
    connection = FakeStrictRedis()
    queue = rq.Queue(connection=connection)
    scheduler = rq_scheduler.Scheduler(queue=queue, connection=connection)
    schedule(scheduler)

    for job in scheduler.get_jobs():
        scheduler.enqueue_job(job)

    rq.SimpleWorker([queue], connection=connection).work(burst=True)


def test_cron_job_ok(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(lambda s: s.cron("*/5 * * * *", func=ok_job, id="cleanup"))

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


def test_cron_job_error(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(lambda s: s.cron("0 3 * * *", func=failing_job, id="nightly"))

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "error"]

    events = [envelope.get_event() for envelope in envelopes]
    (error,) = [e for e in events if e is not None and e.get("exception")]
    assert error["exception"]["values"][0]["type"] == "ValueError"


def test_cron_job_generated_id(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(lambda s: s.cron("0 * * * *", func=ok_job))

    assert {c["monitor_slug"] for c in _check_ins(envelopes)} == {
        "tests.integrations.rq.test_rq_crons.ok_job"
    }


@pytest.mark.parametrize(
    "interval, schedule",
    [
        (300, {"type": "interval", "value": 5, "unit": "minute"}),
        (7200, {"type": "interval", "value": 2, "unit": "hour"}),
        (86400, {"type": "interval", "value": 1, "unit": "day"}),
    ],
)
def test_interval_job(sentry_init, capture_envelopes, interval, schedule):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(
        lambda s: s.schedule(
            datetime.now(timezone.utc), ok_job, interval=interval, id="poll"
        )
    )

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]
    assert {c["monitor_slug"] for c in check_ins} == {"poll"}
    for check_in in check_ins:
        assert check_in["monitor_config"] == {"schedule": schedule}


@pytest.mark.parametrize("interval", [30, 90])
def test_unsupported_interval_not_monitored(sentry_init, capture_envelopes, interval):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(
        lambda s: s.schedule(
            datetime.now(timezone.utc), ok_job, interval=interval, id="poll"
        )
    )

    assert _check_ins(envelopes) == []


def test_unsupported_cron_string_not_monitored(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(lambda s: s.cron("0 0 1 * 1", func=ok_job, id="first-or-monday"))

    assert _check_ins(envelopes) == []


def test_local_time_cron_not_monitored(monkeypatch, sentry_init, capture_envelopes):
    _set_local_timezone(monkeypatch, "America/New_York")
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(lambda s: s.cron("0 * * * *", func=ok_job, id="hourly"))

    assert _check_ins(envelopes) == []


def test_repeat_job_not_monitored(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run(lambda s: s.cron("0 * * * *", func=ok_job, id="limited", repeat=3))

    assert _check_ins(envelopes) == []


def test_perform_job_raises(monkeypatch, sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    def handle_execution_ended(*args, **kwargs):
        raise ConnectionError("redis down")

    connection = FakeStrictRedis()
    queue = rq.Queue(connection=connection)
    scheduler = rq_scheduler.Scheduler(queue=queue, connection=connection)
    job = scheduler.cron("0 * * * *", func=ok_job, id="hourly")
    scheduler.enqueue_job(job)
    worker = rq.SimpleWorker([queue], connection=connection)
    # Raises from rq's own exception handler too.
    monkeypatch.setattr(worker, "handle_execution_ended", handle_execution_ended)

    with pytest.raises(ConnectionError):
        worker.perform_job(job, queue)

    in_progress, error = _check_ins(envelopes)
    assert in_progress["status"] == "in_progress"
    assert error["status"] == "error"
    assert error["check_in_id"] == in_progress["check_in_id"]
    assert error["monitor_config"] == in_progress["monitor_config"]


def test_one_off_jobs_not_monitored(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    def schedule(scheduler):
        scheduler.enqueue_at(datetime.now(timezone.utc), ok_job)
        scheduler.enqueue_in(timedelta(seconds=0), ok_job)
        scheduler.schedule(datetime.now(timezone.utc), ok_job)

    _run(schedule)

    assert _check_ins(envelopes) == []


def test_not_monitored_by_default(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration()])
    envelopes = capture_envelopes()

    _run(lambda s: s.cron("0 * * * *", func=ok_job, id="cleanup"))

    assert _check_ins(envelopes) == []


def test_exclude_cron_jobs(sentry_init, capture_envelopes):
    sentry_init(
        integrations=[
            RqIntegration(monitor_cron_jobs=True, exclude_cron_jobs=["clean.*"])
        ]
    )
    envelopes = capture_envelopes()

    def schedule(scheduler):
        scheduler.cron("0 * * * *", func=ok_job, id="cleanup")
        scheduler.cron("0 * * * *", func=ok_job, id="report")

    _run(schedule)

    assert {c["monitor_slug"] for c in _check_ins(envelopes)} == {"report"}


@pytest.mark.parametrize(
    "cron_string",
    [
        "* * * * *",
        "*/5 * * * *",
        "0 9-17 * * 1-5",
        "30 2 1,15 * *",
        "0 0 * 1/3 *",
        "5-55/10 * * * *",
        "0 0 * * 5-7",
    ],
)
def test_get_crontab_config(cron_string):
    assert _get_crontab_config(cron_string) == {
        "schedule": {"type": "crontab", "value": cron_string},
        "timezone": "UTC",
    }


@pytest.mark.parametrize(
    "cron_string",
    [
        # seconds
        "*/10 * * * * *",
        "@daily",
        "0 0 L * *",
        "0 0 * * MON",
        # day of month and day of week
        "0 0 1 * 1",
        # read differently by rq-scheduler and Sentry
        "0 0 * * 1/2",
        "0 0 * * 0-0",
        "0 0 * * 5-1",
        "10-10/5 * * * *",
    ],
)
def test_get_crontab_config_not_representable(cron_string):
    assert _get_crontab_config(cron_string) is None


@pytest.mark.parametrize("tz", ["America/New_York", "Europe/London"])
def test_get_crontab_config_local_time(monkeypatch, tz):
    _set_local_timezone(monkeypatch, tz)
    assert _get_crontab_config("0 0 * * *") is None

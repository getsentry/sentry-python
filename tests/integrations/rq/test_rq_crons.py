import pytest
import rq
from fakeredis import FakeStrictRedis

from sentry_sdk.integrations.rq import RqIntegration
from sentry_sdk.utils import parse_version

cron = pytest.importorskip("rq.cron")

RQ_VERSION = parse_version(rq.VERSION)
needs_cron_strings = pytest.mark.skipif(
    RQ_VERSION < (2, 5), reason="cron strings were added in RQ 2.5"
)


def ok_job():
    pass


def failing_job():
    1 / 0


def _check_ins(envelopes):
    return [
        item.payload.json
        for envelope in envelopes
        for item in envelope.items
        if item.headers.get("type") == "check_in"
    ]


def _run_cron_job(**cron_job_kwargs):
    """Have the cron job enqueue once and let a worker run it."""
    connection = FakeStrictRedis()
    cron_job_kwargs.setdefault("func", ok_job)
    cron_job = cron.CronJob(queue_name="default", **cron_job_kwargs)
    cron_job.enqueue(connection)

    queue = rq.Queue("default", connection=connection)
    worker = rq.SimpleWorker([queue], connection=connection)
    worker.work(burst=True)


@needs_cron_strings
def test_cron_string_ok(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run_cron_job(cron="*/5 * * * *")

    check_ins = _check_ins(envelopes)
    assert len(check_ins) == 2
    in_progress, ok = check_ins

    slug = "tests.integrations.rq.test_rq_crons.ok_job"
    assert in_progress["monitor_slug"] == slug
    assert in_progress["status"] == "in_progress"
    assert in_progress["monitor_config"] == {
        "schedule": {"type": "crontab", "value": "*/5 * * * *"},
        "timezone": "UTC",
    }

    assert ok["monitor_slug"] == slug
    assert ok["status"] == "ok"
    assert ok["check_in_id"] == in_progress["check_in_id"]
    assert ok["duration"] >= 0


@needs_cron_strings
def test_cron_string_error(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run_cron_job(func=failing_job, cron="0 3 * * *")

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "error"]
    assert check_ins[0]["check_in_id"] == check_ins[1]["check_in_id"]


@needs_cron_strings
@pytest.mark.parametrize("cron_string", ["@daily", "@hourly"])
def test_cron_string_shorthand(sentry_init, capture_envelopes, cron_string):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run_cron_job(cron=cron_string)

    check_ins = _check_ins(envelopes)
    assert check_ins[0]["monitor_config"]["schedule"] == {
        "type": "crontab",
        "value": cron_string,
    }


@needs_cron_strings
def test_cron_string_with_seconds_is_not_monitored(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run_cron_job(cron="*/5 * * * * 30")

    assert _check_ins(envelopes) == []


@pytest.mark.parametrize(
    "interval, expected",
    [
        (60, (1, "minute")),
        (90 * 60, (90, "minute")),
        (2 * 60 * 60, (2, "hour")),
        (24 * 60 * 60, (1, "day")),
    ],
)
def test_interval(sentry_init, capture_envelopes, interval, expected):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run_cron_job(interval=interval)

    check_ins = _check_ins(envelopes)
    value, unit = expected
    assert check_ins[0]["monitor_config"] == {
        "schedule": {"type": "interval", "value": value, "unit": unit},
    }
    assert [c["status"] for c in check_ins] == ["in_progress", "ok"]


@pytest.mark.parametrize("interval", [30, 90])
def test_interval_not_whole_minutes(sentry_init, capture_envelopes, interval):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run_cron_job(interval=interval)

    assert _check_ins(envelopes) == []


def test_monitoring_is_opt_in(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration()])
    envelopes = capture_envelopes()

    _run_cron_job(interval=60)

    assert _check_ins(envelopes) == []


@pytest.mark.skipif(RQ_VERSION < (2, 11), reason="names were added in RQ 2.11")
def test_cron_job_name_is_slug(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    _run_cron_job(interval=60, name="nightly-cleanup")

    assert [c["monitor_slug"] for c in _check_ins(envelopes)] == [
        "nightly-cleanup",
        "nightly-cleanup",
    ]


def test_regular_jobs_are_not_monitored(sentry_init, capture_envelopes):
    sentry_init(integrations=[RqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    queue = rq.Queue("default", connection=FakeStrictRedis())
    queue.enqueue(ok_job)
    rq.SimpleWorker([queue], connection=queue.connection).work(burst=True)

    assert _check_ins(envelopes) == []

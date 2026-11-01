import time
from datetime import timedelta, timezone

import arq.worker
import pytest
from arq import cron
from arq.connections import ArqRedis
from fakeredis.aioredis import FakeRedis

from sentry_sdk.integrations.arq import ArqIntegration, _get_monitor_config

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


@pytest.fixture(autouse=True)
def patch_fakeredis_info_command():
    from fakeredis._fakesocket import FakeSocket

    if not hasattr(FakeSocket, "info"):
        from fakeredis._commands import command
        from fakeredis._helpers import SimpleString

        @command((SimpleString,), name="info")
        def info(self, section):
            return section

        FakeSocket.info = info


@pytest.fixture(autouse=True)
def utc_local_time(monkeypatch):
    # arq < 0.25 runs cron jobs in local time.
    monkeypatch.setattr(time, "timezone", 0)
    monkeypatch.setattr(time, "daylight", 0)


def _check_ins(envelopes):
    return [
        item.payload.json
        for envelope in envelopes
        for item in envelope.items
        if item.headers.get("type") == "check_in"
    ]


async def ok_job(ctx):
    pass


async def failing_job(ctx):
    raise ValueError("oops")


async def _run_cron_jobs(*cron_jobs, **settings):
    pool = ArqRedis(pool_or_conn=FakeRedis().connection_pool)

    class WorkerSettings:
        redis_pool = pool
        burst = True
        poll_delay = 0

    WorkerSettings.cron_jobs = list(cron_jobs)
    for key, value in settings.items():
        setattr(WorkerSettings, key, value)

    worker = arq.worker.create_worker(WorkerSettings)
    await worker.main()
    await worker.close()


@pytest.mark.asyncio
async def test_cron_job_ok(sentry_init, capture_envelopes):
    sentry_init(integrations=[ArqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    await _run_cron_jobs(
        cron(ok_job, minute={0, 30}, run_at_startup=True), timezone=timezone.utc
    )

    in_progress, ok = _check_ins(envelopes)
    assert in_progress["monitor_slug"] == "ok_job"
    assert in_progress["status"] == "in_progress"
    assert in_progress["monitor_config"] == {
        "schedule": {"type": "crontab", "value": "0,30 * * * *"},
        "timezone": "UTC",
    }
    assert ok["monitor_slug"] == "ok_job"
    assert ok["status"] == "ok"
    assert ok["check_in_id"] == in_progress["check_in_id"]
    assert ok["duration"] >= 0


@pytest.mark.asyncio
async def test_cron_job_error(sentry_init, capture_envelopes):
    sentry_init(integrations=[ArqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    await _run_cron_jobs(
        cron(failing_job, name="nightly", hour=3, minute=0, run_at_startup=True),
        timezone=timezone.utc,
    )

    check_ins = _check_ins(envelopes)
    assert [c["status"] for c in check_ins] == ["in_progress", "error"]
    assert {c["monitor_slug"] for c in check_ins} == {"nightly"}

    events = [envelope.get_event() for envelope in envelopes]
    (error,) = [e for e in events if e is not None and e.get("exception")]
    assert error["exception"]["values"][0]["type"] == "ValueError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, tz",
    [
        # Every 10 seconds can't be expressed as a crontab.
        ({"second": {0, 10, 20, 30, 40, 50}}, timezone.utc),
        ({"day": 1, "weekday": "mon"}, timezone.utc),
        ({"minute": 0}, timezone(timedelta(hours=5, minutes=30))),
    ],
)
async def test_unsupported_schedule_not_monitored(
    sentry_init, capture_envelopes, monkeypatch, kwargs, tz
):
    monkeypatch.setattr(time, "timezone", -tz.utcoffset(None).total_seconds())
    sentry_init(integrations=[ArqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()
    calls = []

    async def job(ctx):
        calls.append(1)

    await _run_cron_jobs(
        cron(job, name="job", run_at_startup=True, **kwargs), timezone=tz
    )

    assert calls
    assert _check_ins(envelopes) == []


@pytest.mark.asyncio
async def test_cron_jobs_not_monitored_by_default(sentry_init, capture_envelopes):
    sentry_init(integrations=[ArqIntegration()])
    envelopes = capture_envelopes()

    await _run_cron_jobs(cron(ok_job, run_at_startup=True))

    assert _check_ins(envelopes) == []


@pytest.mark.asyncio
async def test_exclude_cron_jobs(sentry_init, capture_envelopes):
    sentry_init(
        integrations=[
            ArqIntegration(monitor_cron_jobs=True, exclude_cron_jobs=["ok_.*"])
        ]
    )
    envelopes = capture_envelopes()

    await _run_cron_jobs(
        cron(ok_job, run_at_startup=True),
        cron(ok_job, name="other", run_at_startup=True),
        timezone=timezone.utc,
    )

    assert {c["monitor_slug"] for c in _check_ins(envelopes)} == {"other"}


@pytest.mark.asyncio
async def test_cron_job_monitored_once(sentry_init, capture_envelopes):
    sentry_init(integrations=[ArqIntegration(monitor_cron_jobs=True)])
    envelopes = capture_envelopes()

    cron_job = cron(ok_job, run_at_startup=True)
    await _run_cron_jobs(cron_job, timezone=timezone.utc)
    cron_job.next_run = None
    await _run_cron_jobs(cron_job, timezone=timezone.utc)

    statuses = [c["status"] for c in _check_ins(envelopes)]
    assert statuses[:4] == ["in_progress", "ok", "in_progress", "ok"]


class _Worker:
    def __init__(self, timezone):
        self.timezone = timezone


class _OldWorker:
    pass


@pytest.mark.parametrize(
    "kwargs, crontab",
    [
        ({}, "* * * * *"),
        ({"minute": 15}, "15 * * * *"),
        ({"hour": {9, 17}, "minute": 0}, "0 9,17 * * *"),
        ({"day": 1, "hour": 0, "minute": 0}, "0 0 1 * *"),
        ({"month": {1, 7}, "day": 1, "hour": 0, "minute": 0}, "0 0 1 1,7 *"),
        # arq: Monday = 0, cron: Sunday = 0
        ({"weekday": 0, "hour": 8, "minute": 0}, "0 8 * * 1"),
        ({"weekday": {5, 6}, "hour": 8, "minute": 0}, "0 8 * * 0,6"),
        ({"weekday": "sun", "hour": 8, "minute": 0}, "0 8 * * 0"),
        ({"weekday": "fri", "minute": 0, "second": {0}}, "0 * * * 5"),
    ],
)
def test_get_monitor_config(kwargs, crontab):
    assert _get_monitor_config(cron(ok_job, **kwargs), _Worker(timezone.utc)) == {
        "schedule": {"type": "crontab", "value": crontab},
        "timezone": "UTC",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"second": None},
        {"second": 30},
        {"second": {0, 30}},
        # arq requires both to match, cron either.
        {"day": 1, "weekday": 0},
    ],
)
def test_get_monitor_config_not_representable(kwargs):
    assert _get_monitor_config(cron(ok_job, **kwargs), _Worker(timezone.utc)) is None


@pytest.mark.parametrize(
    "tz, name",
    [
        (timezone.utc, "UTC"),
        (timezone(timedelta(hours=2)), "Etc/GMT-2"),
        (timezone(timedelta(hours=-7)), "Etc/GMT+7"),
        (timezone(timedelta(hours=5, minutes=30)), None),
    ],
)
def test_get_monitor_config_timezone(tz, name):
    config = _get_monitor_config(cron(ok_job, minute=0), _Worker(tz))
    if name is None:
        assert config is None
    else:
        assert config["timezone"] == name


@pytest.mark.skipif(ZoneInfo is None, reason="zoneinfo not available")
def test_get_monitor_config_zoneinfo():
    config = _get_monitor_config(
        cron(ok_job, minute=0), _Worker(ZoneInfo("Europe/Vienna"))
    )
    assert config["timezone"] == "Europe/Vienna"


@pytest.mark.parametrize(
    "tz_offset, daylight, name",
    [(0, 0, "UTC"), (0, 1, None), (18000, 1, None)],
)
def test_get_monitor_config_local_time(monkeypatch, tz_offset, daylight, name):
    monkeypatch.setattr(time, "timezone", tz_offset)
    monkeypatch.setattr(time, "altzone", tz_offset - 3600)
    monkeypatch.setattr(time, "daylight", daylight)
    config = _get_monitor_config(cron(ok_job, minute=0), _OldWorker())
    if name is None:
        assert config is None
    else:
        assert config["timezone"] == name

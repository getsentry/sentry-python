from sentry_sdk.integrations.rq import RqIntegration

import pytest

from fakeredis import FakeStrictRedis
import rq


@pytest.fixture(autouse=True)
def _patch_rq_get_server_version(monkeypatch):
    """
    Patch up RQ 1.5 to work with fakeredis.

    https://github.com/jamesls/fakeredis/issues/273
    """

    from distutils.version import StrictVersion

    for k in "rq.job.Job.get_redis_server_version", "rq.worker.Worker.get_redis_server_version":
        monkeypatch.setattr(k, lambda _: StrictVersion("4.0.0"))


def crashing_job(foo):
    1 / 0


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[RqIntegration()])
    events = capture_events()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    (event,) = events

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "rq"
    assert exception["stacktrace"]["frames"][-1]["vars"]["foo"] == "42"

    assert event["transaction"] == "tests.integrations.rq.test_rq.crashing_job"
    assert event["extra"]["rq-job"] == {
        "args": [],
        "description": "tests.integrations.rq.test_rq.crashing_job(foo=42)",
        "func": "tests.integrations.rq.test_rq.crashing_job",
        "job_id": event["extra"]["rq-job"]["job_id"],
        "kwargs": {"foo": 42},
    }


def test_transport_shutdown(sentry_init, capture_events_forksafe):
    sentry_init(integrations=[RqIntegration()])

    events = capture_events_forksafe()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.Worker([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    event = events.read_event()
    events.read_flush()

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

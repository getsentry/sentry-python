from sentry_sdk.integrations.rq import RqIntegration

import pytest

from fakeredis import FakeStrictRedis
from rq import SimpleWorker, Queue


@pytest.fixture
def run_job():
    queue = Queue(connection=FakeStrictRedis())
    worker = SimpleWorker([queue], connection=queue.connection)

    def inner(fn, *a, **kw):
        job = queue.enqueue(fn, *a, **kw)
        worker.work(burst=True)
        return job

    return inner


def crashing_job(foo):
    1 / 0


def test_basic(sentry_init, capture_events, run_job):
    sentry_init(integrations=[RqIntegration()])

    events = capture_events()
    run_job(crashing_job, foo=42)
    event, = events

    exception, = event["exception"]["values"]
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

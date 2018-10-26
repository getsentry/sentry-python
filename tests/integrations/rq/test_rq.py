from sentry_sdk.integrations.rq import RqIntegration

import pytest
import multiprocessing

from fakeredis import FakeStrictRedis
import rq

from sentry_sdk import Hub


def crashing_job(foo):
    1 / 0


@pytest.mark.parametrize("worker_cls", [rq.SimpleWorker, rq.Worker])
def test_basic(sentry_init, worker_cls):

    sentry_init(integrations=[RqIntegration()])

    events = multiprocessing.Queue()

    def capture_event(event):
        events.put_nowait(event)

    Hub.current.client.transport.capture_event = capture_event

    shutdown_called = multiprocessing.Queue()

    def shutdown(timeout, callback=None):
        shutdown_called.put_nowait(1)
        callback(0, timeout)

    Hub.current.client.transport.shutdown = shutdown

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = worker_cls([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    event = events.get_nowait()
    assert events.empty()

    if worker_cls is rq.Worker:
        assert shutdown_called.get_nowait() == 1
    assert shutdown_called.empty()

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

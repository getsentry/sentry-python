from sentry_sdk.integrations.rq import RqIntegration

import os
import json

from fakeredis import FakeStrictRedis
import rq

from sentry_sdk import Hub


def crashing_job(foo):
    1 / 0


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[RqIntegration()])
    events = capture_events()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

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


def test_transport_shutdown(sentry_init):
    sentry_init(integrations=[RqIntegration()])

    events_r, events_w = os.pipe()
    events_r = os.fdopen(events_r, "rb", 0)
    events_w = os.fdopen(events_w, "wb", 0)

    def capture_event(event):
        events_w.write(json.dumps(event).encode("utf-8"))
        events_w.write(b"\n")

    def shutdown(timeout, callback=None):
        events_w.write(b"shutdown\n")

    Hub.current.client.transport.capture_event = capture_event
    Hub.current.client.transport.shutdown = shutdown

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.Worker([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    event = events_r.readline()
    shutdown = events_r.readline()
    event = json.loads(event)
    assert shutdown == b"shutdown\n"

    exception, = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

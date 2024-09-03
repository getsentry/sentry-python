import json
import os
import pytest

import ray

import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.ray import RayIntegration
from tests.conftest import TestTransport


class RayTestTransport(TestTransport):
    def __init__(self):
        self.envelopes = []
        super().__init__()

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


class RayLoggingTransport(TestTransport):
    def __init__(self):
        super().__init__()

    def capture_envelope(self, envelope: Envelope) -> None:
        print(envelope.serialize().decode("utf-8", "replace"))


def setup_sentry_with_logging_transport():
    setup_sentry(transport=RayLoggingTransport())


def setup_sentry(transport=None):
    sentry_sdk.init(
        integrations=[RayIntegration()],
        transport=RayTestTransport() if transport is None else transport,
        traces_sample_rate=1.0,
    )


@pytest.mark.forked
def test_ray_tracing():
    setup_sentry()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry,
            "working_dir": "./",
        }
    )

    @ray.remote
    def example_task():
        with sentry_sdk.start_span(op="task", description="example task step"):
            ...

        return sentry_sdk.get_client().transport.envelopes

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        worker_envelopes = ray.get(example_task.remote())

    client_envelope = sentry_sdk.get_client().transport.envelopes[0]
    client_transaction = client_envelope.get_transaction_event()
    worker_envelope = worker_envelopes[0]
    worker_transaction = worker_envelope.get_transaction_event()

    assert (
        client_transaction["contexts"]["trace"]["trace_id"]
        == client_transaction["contexts"]["trace"]["trace_id"]
    )

    for span in client_transaction["spans"]:
        assert (
            span["trace_id"]
            == client_transaction["contexts"]["trace"]["trace_id"]
            == client_transaction["contexts"]["trace"]["trace_id"]
        )

    for span in worker_transaction["spans"]:
        assert (
            span["trace_id"]
            == client_transaction["contexts"]["trace"]["trace_id"]
            == client_transaction["contexts"]["trace"]["trace_id"]
        )


@pytest.mark.forked
def test_ray_spans():
    setup_sentry()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry,
            "working_dir": "./",
        }
    )

    @ray.remote
    def example_task():
        return sentry_sdk.get_client().transport.envelopes

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        worker_envelopes = ray.get(example_task.remote())

    client_envelope = sentry_sdk.get_client().transport.envelopes[0]
    client_transaction = client_envelope.get_transaction_event()
    worker_envelope = worker_envelopes[0]
    worker_transaction = worker_envelope.get_transaction_event()

    for span in client_transaction["spans"]:
        assert span["op"] == "queue.submit.ray"
        assert span["origin"] == "auto.queue.ray"

    for span in worker_transaction["spans"]:
        assert span["op"] == "queue.task.ray"
        assert span["origin"] == "auto.queue.ray"


@pytest.mark.forked
def test_ray_errors():
    setup_sentry_with_logging_transport()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry_with_logging_transport,
            "working_dir": "./",
        }
    )

    @ray.remote
    def example_task():
        1 / 0

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        with pytest.raises(ZeroDivisionError):
            future = example_task.remote()
            ray.get(future)

    job_id = future.job_id().hex()

    # Read the worker log output containing the error
    log_dir = "/tmp/ray/session_latest/logs/"
    log_file = [
        f
        for f in os.listdir(log_dir)
        if "worker" in f and job_id in f and f.endswith(".out")
    ][0]
    with open(os.path.join(log_dir, log_file), "r") as file:
        lines = file.readlines()
        # parse error object from log line
        error = json.loads(lines[4][:-1])

    assert error["level"] == "error"
    assert (
        error["transaction"]
        == "tests.integrations.ray.test_ray.test_ray_errors.<locals>.example_task"
    )  # its in the worker, not the client thus not "ray test transaction"
    assert error["exception"]["values"][0]["mechanism"]["type"] == "ray"
    assert not error["exception"]["values"][0]["mechanism"]["handled"]


@pytest.mark.forked
def test_ray_actor():
    setup_sentry()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry,
            "working_dir": "./",
        }
    )

    @ray.remote
    class Counter:
        def __init__(self):
            self.n = 0

        def increment(self):
            with sentry_sdk.start_span(op="task", description="example task step"):
                self.n += 1

            return sentry_sdk.get_client().transport.envelopes

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        counter = Counter.remote()
        worker_envelopes = ray.get(counter.increment.remote())

    # Currently no transactions/spans are captured in actors
    assert worker_envelopes == []

    client_envelope = sentry_sdk.get_client().transport.envelopes[0]
    client_transaction = client_envelope.get_transaction_event()

    assert (
        client_transaction["contexts"]["trace"]["trace_id"]
        == client_transaction["contexts"]["trace"]["trace_id"]
    )

    for span in client_transaction["spans"]:
        assert (
            span["trace_id"]
            == client_transaction["contexts"]["trace"]["trace_id"]
            == client_transaction["contexts"]["trace"]["trace_id"]
        )

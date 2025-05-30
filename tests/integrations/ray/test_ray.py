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


def read_error_from_log(job_id):
    log_dir = "/tmp/ray/session_latest/logs/"
    log_file = [
        f
        for f in os.listdir(log_dir)
        if "worker" in f and job_id in f and f.endswith(".out")
    ][0]
    with open(os.path.join(log_dir, log_file), "r") as file:
        lines = file.readlines()

        try:
            # parse error object from log line
            error = json.loads(lines[4][:-1])
        except IndexError:
            error = None

    return error


@pytest.mark.forked
@pytest.mark.parametrize("task_options", [{}, {"num_cpus": 0}])
def test_tracing_in_ray_tasks(task_options):
    setup_sentry()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry,
            "working_dir": "./",
        }
    )

    def example_task():
        with sentry_sdk.start_span(op="task", name="example task step"):
            ...

        return sentry_sdk.get_client().transport.envelopes

    # Setup ray task, calling decorator directly instead of @,
    # to accommodate for test parametrization
    if task_options:
        example_task = ray.remote(**task_options)(example_task)
    else:
        example_task = ray.remote(example_task)

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        worker_envelopes = ray.get(example_task.remote())

    client_envelope = sentry_sdk.get_client().transport.envelopes[0]
    client_transaction = client_envelope.get_transaction_event()
    assert client_transaction["transaction"] == "ray test transaction"
    assert client_transaction["transaction_info"] == {"source": "custom"}

    worker_envelope = worker_envelopes[0]
    worker_transaction = worker_envelope.get_transaction_event()
    assert (
        worker_transaction["transaction"]
        == "tests.integrations.ray.test_ray.test_tracing_in_ray_tasks.<locals>.example_task"
    )
    assert worker_transaction["transaction_info"] == {"source": "task"}

    (span,) = client_transaction["spans"]
    assert span["op"] == "queue.submit.ray"
    assert span["origin"] == "auto.queue.ray"
    assert (
        span["description"]
        == "tests.integrations.ray.test_ray.test_tracing_in_ray_tasks.<locals>.example_task"
    )
    assert span["parent_span_id"] == client_transaction["contexts"]["trace"]["span_id"]
    assert span["trace_id"] == client_transaction["contexts"]["trace"]["trace_id"]

    (span,) = worker_transaction["spans"]
    assert span["op"] == "task"
    assert span["origin"] == "manual"
    assert span["description"] == "example task step"
    assert span["parent_span_id"] == worker_transaction["contexts"]["trace"]["span_id"]
    assert span["trace_id"] == worker_transaction["contexts"]["trace"]["trace_id"]

    assert (
        client_transaction["contexts"]["trace"]["trace_id"]
        == worker_transaction["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.forked
def test_errors_in_ray_tasks():
    setup_sentry_with_logging_transport()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry_with_logging_transport,
            "working_dir": "./",
        }
    )

    # Setup ray task
    @ray.remote
    def example_task():
        1 / 0

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        with pytest.raises(ZeroDivisionError):
            future = example_task.remote()
            ray.get(future)

    job_id = future.job_id().hex()
    error = read_error_from_log(job_id)

    assert error["level"] == "error"
    assert (
        error["transaction"]
        == "tests.integrations.ray.test_ray.test_errors_in_ray_tasks.<locals>.example_task"
    )
    assert error["exception"]["values"][0]["mechanism"]["type"] == "ray"
    assert not error["exception"]["values"][0]["mechanism"]["handled"]


@pytest.mark.forked
def test_tracing_in_ray_actors():
    setup_sentry()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry,
            "working_dir": "./",
        }
    )

    # Setup ray actor
    @ray.remote
    class Counter:
        def __init__(self):
            self.n = 0

        def increment(self):
            with sentry_sdk.start_span(op="task", name="example actor execution"):
                self.n += 1

            return sentry_sdk.get_client().transport.envelopes

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        counter = Counter.remote()
        worker_envelopes = ray.get(counter.increment.remote())

    client_envelope = sentry_sdk.get_client().transport.envelopes[0]
    client_transaction = client_envelope.get_transaction_event()

    # Spans for submitting the actor task are not created (actors are not supported yet)
    assert client_transaction["spans"] == []

    # Transaction are not yet created when executing ray actors (actors are not supported yet)
    assert worker_envelopes == []


@pytest.mark.forked
def test_errors_in_ray_actors():
    setup_sentry_with_logging_transport()

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry_with_logging_transport,
            "working_dir": "./",
        }
    )

    # Setup ray actor
    @ray.remote
    class Counter:
        def __init__(self):
            self.n = 0

        def increment(self):
            with sentry_sdk.start_span(op="task", name="example actor execution"):
                1 / 0

            return sentry_sdk.get_client().transport.envelopes

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        with pytest.raises(ZeroDivisionError):
            counter = Counter.remote()
            future = counter.increment.remote()
            ray.get(future)

    job_id = future.job_id().hex()
    error = read_error_from_log(job_id)

    # We do not capture errors in ray actors yet
    assert error is None

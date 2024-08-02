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


def setup_sentry():
    sentry_sdk.init(
        integrations=[RayIntegration()],
        transport=RayTestTransport(),
        traces_sample_rate=1.0,
    )


@pytest.mark.forked
def test_ray_tracing():
    setup_sentry()

    @ray.remote
    def example_task():
        with sentry_sdk.start_span(op="task", description="example task step"):
            ...

        return sentry_sdk.get_client().transport.envelopes

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry,
            "working_dir": "./",
        }
    )

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

    @ray.remote
    def example_task():
        ...

        return sentry_sdk.get_client().transport.envelopes

    ray.init(
        runtime_env={
            "worker_process_setup_hook": setup_sentry,
            "working_dir": "./",
        }
    )

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

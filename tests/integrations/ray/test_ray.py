import time

import ray

import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.ray import RayIntegration
from tests.conftest import TestTransport


class RayTestTransport(TestTransport):
    def __init__(self):
        self.events = []
        self.envelopes = []
        super().__init__(self.events.append, self.envelopes.append)


def _setup_ray_sentry():
    sentry_sdk.init(
        traces_sample_rate=1.0,
        integrations=[RayIntegration()],
        transport=RayTestTransport(),
    )


def test_ray():
    _setup_ray_sentry()

    @ray.remote
    def _task():
        with sentry_sdk.start_span(op="task", description="example task step"):
            time.sleep(0.1)
        return sentry_sdk.get_client().transport.envelopes

    ray.init(
        runtime_env=dict(worker_process_setup_hook=_setup_ray_sentry, working_dir="./")
    )

    with sentry_sdk.start_transaction(op="task", name="ray test transaction"):
        worker_envelopes = ray.get(_task.remote())

    _assert_envelopes_are_associated_with_same_trace_id(
        sentry_sdk.Hub.get_client().transport.envelopes[0], worker_envelopes[0]
    )


def _assert_envelopes_are_associated_with_same_trace_id(
    client_side_envelope: Envelope, worker_envelope: Envelope
):
    client_side_envelope_dict = client_side_envelope.get_transaction_event()
    worker_envelope_dict = worker_envelope.get_transaction_event()
    trace_id = client_side_envelope_dict["contexts"]["trace"]["trace_id"]
    for span in client_side_envelope_dict["spans"]:
        assert span["trace_id"] == trace_id
    for span in worker_envelope_dict["spans"]:
        assert span["trace_id"] == trace_id
    assert worker_envelope_dict["contexts"]["trace"]["trace_id"] == trace_id

from __future__ import absolute_import

from concurrent import futures

import grpc
import pytest

from sentry_sdk import Hub, start_transaction
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc.server import ServerInterceptor
from tests.integrations.grpc.test_service_pb2 import TestMessage
from tests.integrations.grpc.test_service_pb2_grpc import (
    TestServiceServicer,
    add_TestServiceServicer_to_server,
    TestServiceStub,
)

PORT = 50051


@pytest.mark.forked
def test_grpc_server_starts_transaction(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events_forksafe()

    server = _set_up()

    with grpc.insecure_channel(f"localhost:{PORT}") as channel:
        stub = TestServiceStub(channel)
        stub.TestServe(TestMessage(text="test"))

    _tear_down(server=server)

    events.write_file.close()
    event = events.read_event()
    span = event["spans"][0]

    assert event["type"] == "transaction"
    assert event["transaction_info"] == {
        "source": "custom",
    }
    assert event["contexts"]["trace"]["op"] == OP.GRPC_SERVER
    assert span["op"] == "test"


@pytest.mark.forked
def test_grpc_server_continues_transaction(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events_forksafe()

    server = _set_up()

    with grpc.insecure_channel(f"localhost:{PORT}") as channel:
        stub = TestServiceStub(channel)

        with start_transaction() as transaction:
            metadata = (
                (
                    "baggage",
                    "sentry-trace_id={trace_id},sentry-environment=test,"
                    "sentry-transaction=test-transaction,sentry-sample_rate=1.0".format(
                        trace_id=transaction.trace_id
                    ),
                ),
                (
                    "sentry-trace",
                    "{trace_id}-{parent_span_id}-{sampled}".format(
                        trace_id=transaction.trace_id,
                        parent_span_id=transaction.span_id,
                        sampled=1,
                    )
                )
            )
            stub.TestServe(TestMessage(text="test"), metadata=metadata)

    _tear_down(server=server)

    events.write_file.close()
    event = events.read_event()
    span = event["spans"][0]

    assert event["type"] == "transaction"
    assert event["transaction_info"] == {
        "source": "custom",
    }
    assert event["contexts"]["trace"]["op"] == OP.GRPC_SERVER
    assert event["contexts"]["trace"]["trace_id"] == transaction.trace_id
    assert span["op"] == "test"


def _set_up():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=2),
        interceptors=[ServerInterceptor(find_name=_find_name)],
    )

    add_TestServiceServicer_to_server(TestService, server)
    server.add_insecure_port(f"[::]:{PORT}")
    server.start()

    return server


def _tear_down(server: grpc.Server):
    server.stop(None)


def _find_name(request):
    return request.__class__


class TestService(TestServiceServicer):
    events = []

    @staticmethod
    def TestServe(request, context):
        hub = Hub.current
        with hub.start_span(op="test", description="test"):
            pass

        return TestMessage(text=request.text)

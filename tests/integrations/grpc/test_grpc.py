from __future__ import absolute_import

import os

from concurrent import futures

import grpc
import pytest

from sentry_sdk import Hub, start_transaction
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc.client import ClientInterceptor
from sentry_sdk.integrations.grpc.server import ServerInterceptor
from tests.integrations.grpc.grpc_test_service_pb2 import gRPCTestMessage
from tests.integrations.grpc.grpc_test_service_pb2_grpc import (
    gRPCTestServiceServicer,
    add_gRPCTestServiceServicer_to_server,
    gRPCTestServiceStub,
)

PORT = 50051
PORT += os.getpid() % 100  # avoid port conflicts when running tests in parallel


@pytest.mark.forked
def test_grpc_server_starts_transaction(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events_forksafe()

    server = _set_up()

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        stub.TestServe(gRPCTestMessage(text="test"))

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

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        stub = gRPCTestServiceStub(channel)

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
                    ),
                ),
            )
            stub.TestServe(gRPCTestMessage(text="test"), metadata=metadata)

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


@pytest.mark.forked
def test_grpc_client_starts_span(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events_forksafe()
    interceptors = [ClientInterceptor()]

    server = _set_up()

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        channel = grpc.intercept_channel(channel, *interceptors)
        stub = gRPCTestServiceStub(channel)

        with start_transaction():
            stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    events.write_file.close()
    events.read_event()
    local_transaction = events.read_event()
    span = local_transaction["spans"][0]

    assert len(local_transaction["spans"]) == 1
    assert span["op"] == OP.GRPC_CLIENT
    assert (
        span["description"]
        == "unary unary call to /grpc_test_server.gRPCTestService/TestServe"
    )
    assert span["data"] == {
        "type": "unary unary",
        "method": "/grpc_test_server.gRPCTestService/TestServe",
        "code": "OK",
    }


@pytest.mark.forked
def test_grpc_client_and_servers_interceptors_integration(
    sentry_init, capture_events_forksafe
):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events_forksafe()
    interceptors = [ClientInterceptor()]

    server = _set_up()

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        channel = grpc.intercept_channel(channel, *interceptors)
        stub = gRPCTestServiceStub(channel)

        with start_transaction():
            stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    events.write_file.close()
    server_transaction = events.read_event()
    local_transaction = events.read_event()

    assert (
        server_transaction["contexts"]["trace"]["trace_id"]
        == local_transaction["contexts"]["trace"]["trace_id"]
    )


def _set_up():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=2),
        interceptors=[ServerInterceptor(find_name=_find_name)],
    )

    add_gRPCTestServiceServicer_to_server(TestService, server)
    server.add_insecure_port("[::]:{}".format(PORT))
    server.start()

    return server


def _tear_down(server: grpc.Server):
    server.stop(None)


def _find_name(request):
    return request.__class__


class TestService(gRPCTestServiceServicer):
    events = []

    @staticmethod
    def TestServe(request, context):  # noqa: N802
        hub = Hub.current
        with hub.start_span(op="test", description="test"):
            pass

        return gRPCTestMessage(text=request.text)

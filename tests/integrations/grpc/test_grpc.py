from __future__ import absolute_import

import os
from typing import List, Optional
from concurrent import futures
from unittest.mock import Mock

import grpc
import pytest

from sentry_sdk import Hub, start_transaction
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc import GRPCIntegration
from tests.conftest import ApproxDict
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
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
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
def test_grpc_server_other_interceptors(sentry_init, capture_events_forksafe):
    """Ensure compatibility with additional server interceptors."""
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()
    mock_intercept = lambda continuation, handler_call_details: continuation(
        handler_call_details
    )
    mock_interceptor = Mock()
    mock_interceptor.intercept_service.side_effect = mock_intercept

    server = _set_up(interceptors=[mock_interceptor])

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    mock_interceptor.intercept_service.assert_called_once()

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
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
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
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server = _set_up()

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
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
    assert span["data"] == ApproxDict(
        {
            "type": "unary unary",
            "method": "/grpc_test_server.gRPCTestService/TestServe",
            "code": "OK",
        }
    )


@pytest.mark.forked
def test_grpc_client_unary_stream_starts_span(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server = _set_up()

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        stub = gRPCTestServiceStub(channel)

        with start_transaction():
            [el for el in stub.TestUnaryStream(gRPCTestMessage(text="test"))]

    _tear_down(server=server)

    events.write_file.close()
    local_transaction = events.read_event()
    span = local_transaction["spans"][0]

    assert len(local_transaction["spans"]) == 1
    assert span["op"] == OP.GRPC_CLIENT
    assert (
        span["description"]
        == "unary stream call to /grpc_test_server.gRPCTestService/TestUnaryStream"
    )
    assert span["data"] == ApproxDict(
        {
            "type": "unary stream",
            "method": "/grpc_test_server.gRPCTestService/TestUnaryStream",
        }
    )


# using unittest.mock.Mock not possible because grpc verifies
# that the interceptor is of the correct type
class MockClientInterceptor(grpc.UnaryUnaryClientInterceptor):
    call_counter = 0

    def intercept_unary_unary(self, continuation, client_call_details, request):
        self.__class__.call_counter += 1
        return continuation(client_call_details, request)


@pytest.mark.forked
def test_grpc_client_other_interceptor(sentry_init, capture_events_forksafe):
    """Ensure compatibility with additional client interceptors."""
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server = _set_up()

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        channel = grpc.intercept_channel(channel, MockClientInterceptor())
        stub = gRPCTestServiceStub(channel)

        with start_transaction():
            stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    assert MockClientInterceptor.call_counter == 1

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
    assert span["data"] == ApproxDict(
        {
            "type": "unary unary",
            "method": "/grpc_test_server.gRPCTestService/TestServe",
            "code": "OK",
        }
    )


@pytest.mark.forked
def test_grpc_client_and_servers_interceptors_integration(
    sentry_init, capture_events_forksafe
):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server = _set_up()

    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
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


@pytest.mark.forked
def test_stream_stream(sentry_init):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    _set_up()
    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        response_iterator = stub.TestStreamStream(iter((gRPCTestMessage(text="test"),)))
        for response in response_iterator:
            assert response.text == "test"


def test_stream_unary(sentry_init):
    """Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    _set_up()
    with grpc.insecure_channel("localhost:{}".format(PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        response = stub.TestStreamUnary(iter((gRPCTestMessage(text="test"),)))
        assert response.text == "test"


def _set_up(interceptors: Optional[List[grpc.ServerInterceptor]] = None):
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=2),
        interceptors=interceptors,
    )

    add_gRPCTestServiceServicer_to_server(TestService(), server)
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

    @staticmethod
    def TestUnaryStream(request, context):  # noqa: N802
        for _ in range(3):
            yield gRPCTestMessage(text=request.text)

    @staticmethod
    def TestStreamStream(request, context):  # noqa: N802
        for r in request:
            yield r

    @staticmethod
    def TestStreamUnary(request, context):  # noqa: N802
        requests = [r for r in request]
        return requests.pop()

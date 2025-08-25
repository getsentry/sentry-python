import grpc
import pytest

from concurrent import futures
from typing import List, Optional, Tuple
from unittest.mock import Mock

from sentry_sdk import start_span
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc import GRPCIntegration
from tests.conftest import ApproxDict
from tests.integrations.grpc.grpc_test_service_pb2 import gRPCTestMessage
from tests.integrations.grpc.grpc_test_service_pb2_grpc import (
    add_gRPCTestServiceServicer_to_server,
    gRPCTestServiceServicer,
    gRPCTestServiceStub,
)


# Set up in-memory channel instead of network-based
def _set_up(
    interceptors: Optional[List[grpc.ServerInterceptor]] = None,
) -> Tuple[grpc.Server, grpc.Channel]:
    """
    Sets up a gRPC server and returns both the server and a channel connected to it.
    This eliminates network dependencies and makes tests more reliable.
    """
    # Create server with thread pool
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=2),
        interceptors=interceptors,
    )

    # Add our test service to the server
    servicer = TestService()
    add_gRPCTestServiceServicer_to_server(servicer, server)

    # Use dynamic port allocation instead of hardcoded port
    port = server.add_insecure_port("[::]:0")  # Let gRPC choose an available port
    server.start()

    # Create channel connected to our server
    channel = grpc.insecure_channel(f"localhost:{port}")  # noqa: E231

    return server, channel


def _tear_down(server: grpc.Server):
    server.stop(grace=None)  # Immediate shutdown


@pytest.mark.forked
def test_grpc_server_starts_root_span(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server, channel = _set_up()

    # Use the provided channel
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

    server, channel = _set_up(interceptors=[mock_interceptor])

    # Use the provided channel
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
def test_grpc_server_continues_trace(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    with start_span() as root_span:
        metadata = (
            (
                "baggage",
                "sentry-trace_id={trace_id},sentry-environment=test,"
                "sentry-transaction=test-transaction,sentry-sample_rate=1.0".format(
                    trace_id=root_span.trace_id
                ),
            ),
            (
                "sentry-trace",
                "{trace_id}-{parent_span_id}-{sampled}".format(
                    trace_id=root_span.trace_id,
                    parent_span_id=root_span.span_id,
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
    assert event["contexts"]["trace"]["trace_id"] == root_span.trace_id
    assert span["op"] == "test"


@pytest.mark.forked
def test_grpc_client_starts_span(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    with start_span():
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    events.write_file.close()
    events.read_event()
    local_root_span = events.read_event()
    span = local_root_span["spans"][0]

    assert len(local_root_span["spans"]) == 1
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

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    with start_span():
        [el for el in stub.TestUnaryStream(gRPCTestMessage(text="test"))]

    _tear_down(server=server)

    events.write_file.close()
    local_root_span = events.read_event()
    span = local_root_span["spans"][0]

    assert len(local_root_span["spans"]) == 1
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

    server, channel = _set_up()

    # Intercept the channel
    channel = grpc.intercept_channel(channel, MockClientInterceptor())
    stub = gRPCTestServiceStub(channel)

    with start_span():
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    assert MockClientInterceptor.call_counter == 1

    events.write_file.close()
    events.read_event()
    local_root_span = events.read_event()
    span = local_root_span["spans"][0]

    assert len(local_root_span["spans"]) == 1
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

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    with start_span():
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    events.write_file.close()
    server_root_span = events.read_event()
    local_root_span = events.read_event()

    assert (
        server_root_span["contexts"]["trace"]["trace_id"]
        == local_root_span["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.forked
def test_stream_stream(sentry_init):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    response_iterator = stub.TestStreamStream(iter((gRPCTestMessage(text="test"),)))
    for response in response_iterator:
        assert response.text == "test"

    _tear_down(server=server)


@pytest.mark.forked
def test_stream_unary(sentry_init):
    """
    Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    response = stub.TestStreamUnary(iter((gRPCTestMessage(text="test"),)))
    assert response.text == "test"

    _tear_down(server=server)


@pytest.mark.forked
def test_span_origin(sentry_init, capture_events_forksafe):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    events = capture_events_forksafe()

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    with start_span(name="custom_transaction"):
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    events.write_file.close()

    root_span_from_integration = events.read_event()
    custom_root_span = events.read_event()

    assert root_span_from_integration["contexts"]["trace"]["origin"] == "auto.grpc.grpc"
    assert (
        root_span_from_integration["spans"][0]["origin"] == "auto.grpc.grpc.TestService"
    )  # manually created in TestService, not the instrumentation

    assert custom_root_span["contexts"]["trace"]["origin"] == "manual"
    assert custom_root_span["spans"][0]["origin"] == "auto.grpc.grpc"


class TestService(gRPCTestServiceServicer):
    events = []

    @staticmethod
    def TestServe(request, context):  # noqa: N802
        with start_span(
            op="test",
            name="test",
            origin="auto.grpc.grpc.TestService",
        ):
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

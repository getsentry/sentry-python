from concurrent import futures
from typing import List, Optional, Tuple
from unittest import mock
from unittest.mock import Mock

import grpc
import pytest

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc import GRPCIntegration
from sentry_sdk.integrations.grpc.client import ClientInterceptor
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
def test_grpc_server_starts_transaction(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[0]

    assert spans[1]["attributes"]["sentry.segment.name.source"] == "custom"
    assert spans[1]["attributes"]["sentry.op"] == OP.GRPC_SERVER
    assert span["attributes"]["sentry.op"] == "test"


@pytest.mark.forked
def test_grpc_server_other_interceptors(
    sentry_init,
    capture_items_forksafe,
):
    """Ensure compatibility with additional server interceptors."""
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    mock_intercept = lambda continuation, handler_call_details: continuation(
        handler_call_details
    )
    mock_interceptor = Mock()
    mock_interceptor.intercept_service.side_effect = mock_intercept

    server, channel = _set_up(interceptors=[mock_interceptor])

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    mock_interceptor.intercept_service.assert_called_once()

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[0]

    assert spans[1]["attributes"]["sentry.segment.name.source"] == "custom"
    assert spans[1]["attributes"]["sentry.op"] == OP.GRPC_SERVER
    assert span["attributes"]["sentry.op"] == "test"


@pytest.mark.forked
def test_grpc_server_continues_transaction(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.traces.start_span(name="custom parent") as segment_span:
        metadata = (
            (
                "baggage",
                "sentry-trace_id={trace_id},sentry-environment=test,"
                "sentry-transaction=test-transaction,sentry-sample_rate=1.0".format(
                    trace_id=segment_span.trace_id
                ),
            ),
            (
                "sentry-trace",
                "{trace_id}-{parent_span_id}-{sampled}".format(
                    trace_id=segment_span.trace_id,
                    parent_span_id=segment_span.span_id,
                    sampled=1,
                ),
            ),
        )

        stub.TestServe(gRPCTestMessage(text="test"), metadata=metadata)

    _tear_down(server=server)

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[0]

    assert spans[1]["attributes"]["sentry.segment.name.source"] == "custom"
    assert spans[1]["attributes"]["sentry.op"] == OP.GRPC_SERVER
    assert spans[1]["trace_id"] == segment_span.trace_id
    assert span["attributes"]["sentry.op"] == "test"


@pytest.mark.forked
def test_grpc_client_starts_span(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[2]

    assert len(spans) == 4
    assert span["attributes"]["sentry.op"] == OP.GRPC_CLIENT
    assert (
        span["name"]
        == "unary unary call to /grpc_test_server.gRPCTestService/TestServe"
    )
    assert span["attributes"] == ApproxDict(
        {
            "rpc.method": "/grpc_test_server.gRPCTestService/TestServe",
            "sentry.environment": mock.ANY,
            "sentry.op": "grpc.client",
            "sentry.origin": "auto.grpc.grpc",
            "sentry.release": mock.ANY,
            "sentry.sdk.name": "sentry.python",
            "sentry.sdk.version": mock.ANY,
            "sentry.segment.id": mock.ANY,
            "sentry.segment.name": "custom parent",
            "server.address": mock.ANY,
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
            "rpc.response.status_code": "OK",
        }
    )


@pytest.mark.forked
def test_grpc_client_unary_stream_starts_span(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        [el for el in stub.TestUnaryStream(gRPCTestMessage(text="test"))]

    _tear_down(server=server)

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[0]

    assert len(spans) == 2
    assert span["attributes"]["sentry.op"] == OP.GRPC_CLIENT
    assert (
        span["name"]
        == "unary stream call to /grpc_test_server.gRPCTestService/TestUnaryStream"
    )
    assert span["attributes"] == ApproxDict(
        {
            "rpc.method": "/grpc_test_server.gRPCTestService/TestUnaryStream",
            "sentry.environment": mock.ANY,
            "sentry.op": "grpc.client",
            "sentry.origin": "auto.grpc.grpc",
            "sentry.release": mock.ANY,
            "sentry.sdk.name": "sentry.python",
            "sentry.sdk.version": mock.ANY,
            "sentry.segment.id": mock.ANY,
            "sentry.segment.name": "custom parent",
            "server.address": mock.ANY,
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
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
def test_grpc_client_other_interceptor(
    sentry_init,
    capture_items_forksafe,
):
    """Ensure compatibility with additional client interceptors."""
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Intercept the channel
    channel = grpc.intercept_channel(channel, MockClientInterceptor())
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    assert MockClientInterceptor.call_counter == 1

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[2]

    assert len(spans) == 4
    assert span["attributes"]["sentry.op"] == OP.GRPC_CLIENT
    assert (
        span["name"]
        == "unary unary call to /grpc_test_server.gRPCTestService/TestServe"
    )
    assert span["attributes"] == ApproxDict(
        {
            "rpc.method": "/grpc_test_server.gRPCTestService/TestServe",
            "sentry.environment": mock.ANY,
            "sentry.op": "grpc.client",
            "sentry.origin": "auto.grpc.grpc",
            "sentry.release": mock.ANY,
            "sentry.sdk.name": "sentry.python",
            "sentry.sdk.version": mock.ANY,
            "sentry.segment.id": mock.ANY,
            "sentry.segment.name": "custom parent",
            "server.address": mock.ANY,
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
            "rpc.response.status_code": "OK",
        }
    )


@pytest.mark.forked
def test_prevent_dual_client_interceptor(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Intercept the channel
    channel = grpc.intercept_channel(channel, ClientInterceptor())
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[2]

    assert len(spans) == 4
    assert span["attributes"]["sentry.op"] == OP.GRPC_CLIENT
    assert (
        span["name"]
        == "unary unary call to /grpc_test_server.gRPCTestService/TestServe"
    )
    assert span["attributes"] == ApproxDict(
        {
            "rpc.method": "/grpc_test_server.gRPCTestService/TestServe",
            "rpc.response.status_code": "OK",
        }
    )


@pytest.mark.forked
def test_grpc_client_and_servers_interceptors_integration(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()

    spans = [item["payload"] for item in items if item["type"] == "span"]

    assert spans[1]["is_segment"] is True
    assert spans[3]["is_segment"] is True
    assert spans[1]["trace_id"] == spans[3]["trace_id"]


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
def test_span_origin(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        trace_lifecycle="stream",
    )

    server, channel = _set_up()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        stub.TestServe(gRPCTestMessage(text="test"))

    _tear_down(server=server)

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()

    spans = [item["payload"] for item in items if item["type"] == "span"]

    assert spans[1]["attributes"]["sentry.origin"] == "auto.grpc.grpc"
    assert (
        spans[0]["attributes"]["sentry.origin"] == "auto.grpc.grpc.TestService"
    )  # manually created in TestService, not the instrumentation

    assert spans[3]["attributes"]["sentry.origin"] == "manual"
    assert spans[2]["attributes"]["sentry.origin"] == "auto.grpc.grpc"


class TestService(gRPCTestServiceServicer):
    events = []

    @staticmethod
    def TestServe(request, context):  # noqa: N802
        with sentry_sdk.traces.start_span(
            name="test",
            attributes={
                "sentry.op": "test",
                "sentry.origin": "auto.grpc.grpc.TestService",
            },
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

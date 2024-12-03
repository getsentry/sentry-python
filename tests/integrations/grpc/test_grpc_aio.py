import asyncio
import os

import grpc
import pytest
import pytest_asyncio
import sentry_sdk

from sentry_sdk import start_span, start_transaction
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc import GRPCIntegration
from tests.conftest import ApproxDict
from tests.integrations.grpc.grpc_test_service_pb2 import gRPCTestMessage
from tests.integrations.grpc.grpc_test_service_pb2_grpc import (
    add_gRPCTestServiceServicer_to_server,
    gRPCTestServiceServicer,
    gRPCTestServiceStub,
)

AIO_PORT = 50052
AIO_PORT += os.getpid() % 100  # avoid port conflicts when running tests in parallel


@pytest_asyncio.fixture(scope="function")
async def grpc_server(sentry_init):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    server = grpc.aio.server()
    server.add_insecure_port("[::]:{}".format(AIO_PORT))
    add_gRPCTestServiceServicer_to_server(TestService, server)

    await asyncio.create_task(server.start())

    try:
        yield server
    finally:
        await server.stop(None)


@pytest.mark.asyncio
async def test_noop_for_unimplemented_method(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    server = grpc.aio.server()
    server.add_insecure_port("[::]:{}".format(AIO_PORT))

    await asyncio.create_task(server.start())

    events = capture_events()
    try:
        async with grpc.aio.insecure_channel(
            "localhost:{}".format(AIO_PORT)
        ) as channel:
            stub = gRPCTestServiceStub(channel)
            with pytest.raises(grpc.RpcError) as exc:
                await stub.TestServe(gRPCTestMessage(text="test"))
            assert exc.value.details() == "Method not found!"
    finally:
        await server.stop(None)

    assert not events


@pytest.mark.asyncio
async def test_grpc_server_starts_transaction(grpc_server, capture_events):
    events = capture_events()

    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        await stub.TestServe(gRPCTestMessage(text="test"))

    (event,) = events
    span = event["spans"][0]

    assert event["type"] == "transaction"
    assert event["transaction_info"] == {
        "source": "custom",
    }
    assert event["contexts"]["trace"]["op"] == OP.GRPC_SERVER
    assert span["op"] == "test"


@pytest.mark.asyncio
async def test_grpc_server_continues_transaction(grpc_server, capture_events):
    events = capture_events()

    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)

        with sentry_sdk.start_transaction() as transaction:
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

            await stub.TestServe(gRPCTestMessage(text="test"), metadata=metadata)

    (event, _) = events
    span = event["spans"][0]

    assert event["type"] == "transaction"
    assert event["transaction_info"] == {
        "source": "custom",
    }
    assert event["contexts"]["trace"]["op"] == OP.GRPC_SERVER
    assert event["contexts"]["trace"]["trace_id"] == transaction.trace_id
    assert span["op"] == "test"


@pytest.mark.asyncio
async def test_grpc_server_exception(grpc_server, capture_events):
    events = capture_events()

    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        try:
            await stub.TestServe(gRPCTestMessage(text="exception"))
            raise AssertionError()
        except Exception:
            pass

    (event, _) = events

    assert event["exception"]["values"][0]["type"] == "TestService.TestException"
    assert event["exception"]["values"][0]["value"] == "test"
    assert event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert event["exception"]["values"][0]["mechanism"]["type"] == "grpc"


@pytest.mark.asyncio
async def test_grpc_server_abort(grpc_server, capture_events):
    events = capture_events()

    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        try:
            await stub.TestServe(gRPCTestMessage(text="abort"))
            raise AssertionError()
        except Exception:
            pass

    assert len(events) == 1


@pytest.mark.asyncio
async def test_grpc_client_starts_span(grpc_server, capture_events_forksafe):
    events = capture_events_forksafe()

    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        with start_transaction():
            await stub.TestServe(gRPCTestMessage(text="test"))

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


@pytest.mark.asyncio
async def test_grpc_client_unary_stream_starts_span(
    grpc_server, capture_events_forksafe
):
    events = capture_events_forksafe()

    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        with start_transaction():
            response = stub.TestUnaryStream(gRPCTestMessage(text="test"))
            [_ async for _ in response]

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


@pytest.mark.asyncio
async def test_stream_stream(grpc_server):
    """
    Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        response = stub.TestStreamStream((gRPCTestMessage(text="test"),))
        async for r in response:
            assert r.text == "test"


@pytest.mark.asyncio
async def test_stream_unary(grpc_server):
    """
    Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        response = await stub.TestStreamUnary((gRPCTestMessage(text="test"),))
        assert response.text == "test"


@pytest.mark.asyncio
async def test_span_origin(grpc_server, capture_events_forksafe):
    events = capture_events_forksafe()

    async with grpc.aio.insecure_channel("localhost:{}".format(AIO_PORT)) as channel:
        stub = gRPCTestServiceStub(channel)
        with start_transaction(name="custom_transaction"):
            await stub.TestServe(gRPCTestMessage(text="test"))

    events.write_file.close()

    transaction_from_integration = events.read_event()
    custom_transaction = events.read_event()

    assert (
        transaction_from_integration["contexts"]["trace"]["origin"] == "auto.grpc.grpc"
    )
    assert (
        transaction_from_integration["spans"][0]["origin"]
        == "auto.grpc.grpc.TestService.aio"
    )  # manually created in TestService, not the instrumentation

    assert custom_transaction["contexts"]["trace"]["origin"] == "manual"
    assert custom_transaction["spans"][0]["origin"] == "auto.grpc.grpc"


class TestService(gRPCTestServiceServicer):
    class TestException(Exception):
        __test__ = False

        def __init__(self):
            super().__init__("test")

    @classmethod
    async def TestServe(cls, request, context):  # noqa: N802
        with start_span(
            op="test",
            name="test",
            origin="auto.grpc.grpc.TestService.aio",
        ):
            pass

        if request.text == "exception":
            raise cls.TestException()

        if request.text == "abort":
            await context.abort(grpc.StatusCode.ABORTED)

        return gRPCTestMessage(text=request.text)

    @classmethod
    async def TestUnaryStream(cls, request, context):  # noqa: N802
        for _ in range(3):
            yield gRPCTestMessage(text=request.text)

    @classmethod
    async def TestStreamStream(cls, request, context):  # noqa: N802
        async for r in request:
            yield r

    @classmethod
    async def TestStreamUnary(cls, request, context):  # noqa: N802
        requests = [r async for r in request]
        return requests.pop()

from __future__ import absolute_import

import asyncio
import os

import grpc
import pytest
import pytest_asyncio
import sentry_sdk

from sentry_sdk import Hub
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc import GRPCIntegration
from tests.integrations.grpc.grpc_test_service_pb2 import gRPCTestMessage
from tests.integrations.grpc.grpc_test_service_pb2_grpc import (
    gRPCTestServiceServicer,
    add_gRPCTestServiceServicer_to_server,
    gRPCTestServiceStub,
)

AIO_PORT = 50052
AIO_PORT += os.getpid() % 100  # avoid port conflicts when running tests in parallel


@pytest.fixture(scope="function")
def event_loop(request):
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def grpc_server(sentry_init, event_loop):
    sentry_init(traces_sample_rate=1.0, integrations=[GRPCIntegration()])
    server = grpc.aio.server()
    server.add_insecure_port(f"[::]:{AIO_PORT}")
    add_gRPCTestServiceServicer_to_server(TestService, server)

    await event_loop.create_task(server.start())

    try:
        yield server
    finally:
        await server.stop(None)


@pytest.mark.asyncio
async def test_grpc_server_starts_transaction(capture_events, grpc_server):
    events = capture_events()

    async with grpc.aio.insecure_channel(f"localhost:{AIO_PORT}") as channel:
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
async def test_grpc_server_continues_transaction(capture_events, grpc_server):
    events = capture_events()

    async with grpc.aio.insecure_channel(f"localhost:{AIO_PORT}") as channel:
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
async def test_grpc_server_exception(sentry_init, capture_events, grpc_server):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    async with grpc.aio.insecure_channel(f"localhost:{AIO_PORT}") as channel:
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
    assert event["exception"]["values"][0]["mechanism"]["type"] == "gRPC"


class TestService(gRPCTestServiceServicer):
    class TestException(Exception):
        def __init__(self):
            super().__init__("test")

    @classmethod
    async def TestServe(cls, request, context):  # noqa: N802
        hub = Hub.current
        with hub.start_span(op="test", description="test"):
            pass

        if request.text == "exception":
            raise cls.TestException()

        return gRPCTestMessage(text=request.text)

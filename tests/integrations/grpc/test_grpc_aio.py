import asyncio
from typing import Optional
from unittest import mock

import grpc
import pytest
import pytest_asyncio
from sentry_conventions.attributes import ATTRIBUTE_NAMES

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc import GRPCIntegration
from tests.conftest import ApproxDict
from tests.integrations.grpc.grpc_test_service_pb2 import gRPCTestMessage
from tests.integrations.grpc.grpc_test_service_pb2_grpc import (
    add_gRPCTestServiceServicer_to_server,
    gRPCTestServiceServicer,
    gRPCTestServiceStub,
)


@pytest_asyncio.fixture(scope="function")
async def grpc_server_and_channel(sentry_init):
    """
    Creates an async gRPC server and a channel connected to it.
    Returns both for use in tests, and cleans up afterward.
    """
    channel: "Optional[grpc.aio.Channel]" = None
    server: "Optional[grpc.aio.Server]" = None

    async def inner():
        nonlocal server, channel

        sentry_init(
            traces_sample_rate=1.0,
            integrations=[GRPCIntegration()],
        )

        # Create server
        server = grpc.aio.server()

        # Let gRPC choose a free port instead of hardcoding it
        port = server.add_insecure_port("[::]:0")

        # Add service implementation
        add_gRPCTestServiceServicer_to_server(TestService, server)

        # Start the server
        await asyncio.create_task(server.start())

        # Create channel connected to our server
        channel = grpc.aio.insecure_channel(f"localhost:{port}")

        return server, channel

    try:
        yield inner
    finally:
        if channel is not None:
            await channel.close()

        if server is not None:
            await server.stop(None)


@pytest.mark.asyncio
async def test_noop_for_unimplemented_method(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
    )

    # Create empty server with no services
    server = grpc.aio.server()
    port = server.add_insecure_port("[::]:0")  # Let gRPC choose a free port
    await asyncio.create_task(server.start())
    items = capture_items("span")

    try:
        async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
            stub = gRPCTestServiceStub(channel)
            with pytest.raises(grpc.RpcError) as exc:
                await stub.TestServe(gRPCTestMessage(text="test"))
            assert exc.value.details() == "Method not found!"
    finally:
        await server.stop(None)

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 0  # No client span created without an active span.


@pytest.mark.asyncio
async def test_grpc_server_starts_transaction(
    grpc_server_and_channel,
    capture_items,
):
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items("span")

    await stub.TestServe(gRPCTestMessage(text="test"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]

    assert (
        spans[1]["attributes"][ATTRIBUTE_NAMES.SENTRY_SEGMENT_NAME_SOURCE] == "custom"
    )
    assert spans[1]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.GRPC_SERVER
    assert span["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "test"


@pytest.mark.asyncio
async def test_grpc_server_continues_transaction(
    grpc_server_and_channel,
    capture_items,
):
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items("span")

    with sentry_sdk.start_span(name="custom parent") as segment_span:
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

        await stub.TestServe(gRPCTestMessage(text="test"), metadata=metadata)

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]

    assert (
        spans[1]["attributes"][ATTRIBUTE_NAMES.SENTRY_SEGMENT_NAME_SOURCE] == "custom"
    )
    assert spans[1]["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.GRPC_SERVER
    assert spans[1]["trace_id"] == segment_span.trace_id
    assert span["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == "test"


@pytest.mark.asyncio
async def test_grpc_server_exception(
    grpc_server_and_channel,
    capture_items,
):
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items("event")

    try:
        await stub.TestServe(gRPCTestMessage(text="exception"))
        raise AssertionError()
    except Exception:
        pass

    (event,) = (item.payload for item in items)

    assert event["exception"]["values"][0]["type"] == "TestService.TestException"
    assert event["exception"]["values"][0]["value"] == "test"
    assert event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert event["exception"]["values"][0]["mechanism"]["type"] == "grpc"


@pytest.mark.asyncio
async def test_grpc_server_abort(grpc_server_and_channel, capture_items):
    _, channel = await grpc_server_and_channel()
    items = capture_items("span")

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    try:
        await stub.TestServe(gRPCTestMessage(text="abort"))
        raise AssertionError()
    except Exception:
        pass

    # Add a small delay to allow events to be collected
    await asyncio.sleep(0.1)

    assert len(items) >= 1


@pytest.mark.asyncio
async def test_grpc_client_starts_span(
    grpc_server_and_channel,
    capture_items_forksafe,
):
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.start_span(name="custom parent") as span:
        await stub.TestServe(gRPCTestMessage(text="test"))

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[2]

    assert len(spans) == 4
    assert span["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.GRPC_CLIENT
    assert (
        span["name"]
        == "unary unary call to /grpc_test_server.gRPCTestService/TestServe"
    )
    assert span["attributes"] == ApproxDict(
        {
            ATTRIBUTE_NAMES.RPC_METHOD: "/grpc_test_server.gRPCTestService/TestServe",
            ATTRIBUTE_NAMES.SENTRY_ENVIRONMENT: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_OP: "grpc.client",
            ATTRIBUTE_NAMES.SENTRY_ORIGIN: "auto.grpc.grpc",
            ATTRIBUTE_NAMES.SENTRY_RELEASE: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_SDK_NAME: "sentry.python",
            ATTRIBUTE_NAMES.SENTRY_SDK_VERSION: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_SEGMENT_ID: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_SEGMENT_NAME: "custom parent",
            ATTRIBUTE_NAMES.SERVER_ADDRESS: mock.ANY,
            ATTRIBUTE_NAMES.THREAD_ID: mock.ANY,
            ATTRIBUTE_NAMES.THREAD_NAME: mock.ANY,
            ATTRIBUTE_NAMES.RPC_RESPONSE_STATUS_CODE: "OK",
        }
    )


@pytest.mark.asyncio
async def test_grpc_client_unary_stream_starts_span(
    grpc_server_and_channel,
    capture_items_forksafe,
):
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.start_span(name="custom parent"):
        response = stub.TestUnaryStream(gRPCTestMessage(text="test"))
        [_ async for _ in response]

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()
    spans = [item["payload"] for item in items if item["type"] == "span"]
    span = spans[0]

    assert len(spans) == 2
    assert span["attributes"][ATTRIBUTE_NAMES.SENTRY_OP] == OP.GRPC_CLIENT
    assert (
        span["name"]
        == "unary stream call to /grpc_test_server.gRPCTestService/TestUnaryStream"
    )
    assert span["attributes"] == ApproxDict(
        {
            ATTRIBUTE_NAMES.RPC_METHOD: "/grpc_test_server.gRPCTestService/TestUnaryStream",
            ATTRIBUTE_NAMES.SENTRY_ENVIRONMENT: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_OP: "grpc.client",
            ATTRIBUTE_NAMES.SENTRY_ORIGIN: "auto.grpc.grpc",
            ATTRIBUTE_NAMES.SENTRY_RELEASE: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_SDK_NAME: "sentry.python",
            ATTRIBUTE_NAMES.SENTRY_SDK_VERSION: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_SEGMENT_ID: mock.ANY,
            ATTRIBUTE_NAMES.SENTRY_SEGMENT_NAME: "custom parent",
            ATTRIBUTE_NAMES.SERVER_ADDRESS: mock.ANY,
            ATTRIBUTE_NAMES.THREAD_ID: mock.ANY,
            ATTRIBUTE_NAMES.THREAD_NAME: mock.ANY,
        }
    )


@pytest.mark.asyncio
async def test_stream_stream(grpc_server_and_channel):
    """
    Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    response = stub.TestStreamStream((gRPCTestMessage(text="test"),))
    assert [r.text async for r in response] == ["test"]


@pytest.mark.asyncio
async def test_stream_unary(grpc_server_and_channel):
    """
    Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    response = await stub.TestStreamUnary((gRPCTestMessage(text="test"),))
    assert response.text == "test"


@pytest.mark.asyncio
async def test_span_origin(
    grpc_server_and_channel,
    capture_items_forksafe,
):
    _, channel = await grpc_server_and_channel()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    items = capture_items_forksafe("span")

    with sentry_sdk.start_span(name="custom parent"):
        await stub.TestServe(gRPCTestMessage(text="test"))

    sentry_sdk.flush()
    items.write_file.close()
    items = items.read_event()

    spans = [item["payload"] for item in items if item["type"] == "span"]

    assert spans[1]["attributes"][ATTRIBUTE_NAMES.SENTRY_ORIGIN] == "auto.grpc.grpc"
    assert (
        spans[0]["attributes"][ATTRIBUTE_NAMES.SENTRY_ORIGIN]
        == "auto.grpc.grpc.TestService.aio"
    )  # manually created in TestService, not the instrumentation

    assert spans[3]["attributes"][ATTRIBUTE_NAMES.SENTRY_ORIGIN] == "manual"
    assert spans[2]["attributes"][ATTRIBUTE_NAMES.SENTRY_ORIGIN] == "auto.grpc.grpc"


class TestService(gRPCTestServiceServicer):
    class TestException(Exception):
        __test__ = False

        def __init__(self):
            super().__init__("test")

    @classmethod
    async def TestServe(cls, request, context):
        with sentry_sdk.start_span(
            name="test",
            attributes={
                ATTRIBUTE_NAMES.SENTRY_OP: "test",
                ATTRIBUTE_NAMES.SENTRY_ORIGIN: "auto.grpc.grpc.TestService.aio",
            },
        ):
            pass

        if request.text == "exception":
            raise cls.TestException()

        if request.text == "abort":
            await context.abort(grpc.StatusCode.ABORTED, "Aborted!")

        return gRPCTestMessage(text=request.text)

    @classmethod
    async def TestUnaryStream(cls, request, context):
        for _ in range(3):
            yield gRPCTestMessage(text=request.text)

    @classmethod
    async def TestStreamStream(cls, request, context):
        async for r in request:
            yield r

    @classmethod
    async def TestStreamUnary(cls, request, context):
        requests = [r async for r in request]
        return requests.pop()

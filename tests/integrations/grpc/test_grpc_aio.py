import asyncio
from typing import Optional
from unittest import mock

import grpc
import pytest
import pytest_asyncio

import sentry_sdk
from sentry_sdk import start_span, start_transaction
from sentry_sdk.consts import OP
from sentry_sdk.integrations.grpc import GRPCIntegration
from sentry_sdk.tracing_utils import has_span_streaming_enabled
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

    async def inner(span_streaming: bool):
        nonlocal server, channel

        sentry_init(
            traces_sample_rate=1.0,
            integrations=[GRPCIntegration()],
            _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
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
        channel = grpc.aio.insecure_channel(f"localhost:{port}")  # noqa: E231

        return server, channel

    try:
        yield inner
    finally:
        if channel is not None:
            await channel.close()

        if server is not None:
            await server.stop(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_noop_for_unimplemented_method(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[GRPCIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    # Create empty server with no services
    server = grpc.aio.server()
    port = server.add_insecure_port("[::]:0")  # Let gRPC choose a free port
    await asyncio.create_task(server.start())

    if span_streaming:
        items = capture_items("span")

        try:
            async with grpc.aio.insecure_channel(
                f"localhost:{port}"  # noqa: E231
            ) as channel:
                stub = gRPCTestServiceStub(channel)
                with pytest.raises(grpc.RpcError) as exc:
                    await stub.TestServe(gRPCTestMessage(text="test"))
                assert exc.value.details() == "Method not found!"
        finally:
            await server.stop(None)

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 1  # Only client span present.
    else:
        events = capture_events()

        try:
            async with grpc.aio.insecure_channel(
                f"localhost:{port}"  # noqa: E231
            ) as channel:
                stub = gRPCTestServiceStub(channel)
                with pytest.raises(grpc.RpcError) as exc:
                    await stub.TestServe(gRPCTestMessage(text="test"))
                assert exc.value.details() == "Method not found!"
        finally:
            await server.stop(None)

        assert not events


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_grpc_server_starts_transaction(
    grpc_server_and_channel,
    capture_events,
    capture_items,
    span_streaming,
):
    _, channel = await grpc_server_and_channel(span_streaming)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    if span_streaming:
        items = capture_items("span")

        await stub.TestServe(gRPCTestMessage(text="test"))

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]

        assert spans[1]["attributes"]["sentry.span.source"] == "custom"
        assert spans[1]["attributes"]["sentry.op"] == OP.GRPC_SERVER
        assert span["attributes"]["sentry.op"] == "test"
    else:
        events = capture_events()

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_grpc_server_continues_transaction(
    grpc_server_and_channel,
    capture_events,
    capture_items,
    span_streaming,
):
    _, channel = await grpc_server_and_channel(span_streaming)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    if span_streaming:
        items = capture_items("span")

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

            await stub.TestServe(gRPCTestMessage(text="test"), metadata=metadata)

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]

        assert spans[1]["attributes"]["sentry.span.source"] == "custom"
        assert spans[1]["attributes"]["sentry.op"] == OP.GRPC_SERVER
        assert spans[1]["trace_id"] == segment_span.trace_id
        assert span["attributes"]["sentry.op"] == "test"
    else:
        events = capture_events()

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_grpc_server_exception(
    grpc_server_and_channel,
    capture_events,
    capture_items,
    span_streaming,
):
    _, channel = await grpc_server_and_channel(span_streaming)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    if span_streaming:
        items = capture_items("event")

        try:
            await stub.TestServe(gRPCTestMessage(text="exception"))
            raise AssertionError()
        except Exception:
            pass

        (event,) = (item.payload for item in items if item.type == "event")
    else:
        events = capture_events()

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
async def test_grpc_server_abort(grpc_server_and_channel, capture_events):
    _, channel = await grpc_server_and_channel(False)
    events = capture_events()

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    try:
        await stub.TestServe(gRPCTestMessage(text="abort"))
        raise AssertionError()
    except Exception:
        pass

    # Add a small delay to allow events to be collected
    await asyncio.sleep(0.1)

    assert len(events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_grpc_client_starts_span(
    grpc_server_and_channel,
    capture_events_forksafe,
    capture_items_forksafe,
    span_streaming,
):
    _, channel = await grpc_server_and_channel(span_streaming)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    if span_streaming:
        items = capture_items_forksafe("span")

        with sentry_sdk.traces.start_span(name="custom parent") as span:
            await stub.TestServe(gRPCTestMessage(text="test"))

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
    else:
        events = capture_events_forksafe()

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_grpc_client_unary_stream_starts_span(
    grpc_server_and_channel,
    capture_events_forksafe,
    capture_items_forksafe,
    span_streaming,
):
    _, channel = await grpc_server_and_channel(span_streaming)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    if span_streaming:
        items = capture_items_forksafe("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            response = stub.TestUnaryStream(gRPCTestMessage(text="test"))
            [_ async for _ in response]

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
    else:
        events = capture_events_forksafe()

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
async def test_stream_stream(grpc_server_and_channel):
    """
    Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    _, channel = await grpc_server_and_channel(False)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    response = stub.TestStreamStream((gRPCTestMessage(text="test"),))
    async for r in response:
        assert r.text == "test"


@pytest.mark.asyncio
async def test_stream_unary(grpc_server_and_channel):
    """
    Test to verify stream-stream works.
    Tracing not supported for it yet.
    """
    _, channel = await grpc_server_and_channel(False)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)
    response = await stub.TestStreamUnary((gRPCTestMessage(text="test"),))
    assert response.text == "test"


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_span_origin(
    grpc_server_and_channel,
    capture_events_forksafe,
    capture_items_forksafe,
    span_streaming,
):
    _, channel = await grpc_server_and_channel(span_streaming)

    # Use the provided channel
    stub = gRPCTestServiceStub(channel)

    if span_streaming:
        items = capture_items_forksafe("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            await stub.TestServe(gRPCTestMessage(text="test"))

        sentry_sdk.flush()
        items.write_file.close()
        items = items.read_event()

        spans = [item["payload"] for item in items if item["type"] == "span"]

        assert spans[1]["attributes"]["sentry.origin"] == "auto.grpc.grpc"
        assert (
            spans[0]["attributes"]["sentry.origin"] == "auto.grpc.grpc.TestService.aio"
        )  # manually created in TestService, not the instrumentation

        assert spans[3]["attributes"]["sentry.origin"] == "manual"
        assert spans[2]["attributes"]["sentry.origin"] == "auto.grpc.grpc"
    else:
        events = capture_events_forksafe()

        with start_transaction(name="custom_transaction"):
            await stub.TestServe(gRPCTestMessage(text="test"))

        events.write_file.close()

        transaction_from_integration = events.read_event()
        custom_transaction = events.read_event()

        assert (
            transaction_from_integration["contexts"]["trace"]["origin"]
            == "auto.grpc.grpc"
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
        client = sentry_sdk.get_client()
        span_streaming = has_span_streaming_enabled(client.options)

        if span_streaming:
            with sentry_sdk.traces.start_span(
                name="test",
                attributes={
                    "sentry.op": "test",
                    "sentry.origin": "auto.grpc.grpc.TestService.aio",
                },
            ):
                pass
        else:
            with start_span(
                op="test",
                name="test",
                origin="auto.grpc.grpc.TestService.aio",
            ):
                pass

        if request.text == "exception":
            raise cls.TestException()

        if request.text == "abort":
            await context.abort(grpc.StatusCode.ABORTED, "Aborted!")

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

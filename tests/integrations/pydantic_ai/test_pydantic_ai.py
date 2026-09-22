import asyncio
import json
from typing import Annotated
from unittest.mock import MagicMock

import pytest
from pydantic import Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.pydantic_ai import PydanticAIIntegration
from sentry_sdk.integrations.pydantic_ai.spans.ai_client import (
    ai_client_span,
    update_ai_client_span,
)
from sentry_sdk.integrations.pydantic_ai.utils import (
    get_current_agent,
    pop_agent,
    push_agent,
)
from sentry_sdk.utils import package_version

# The SDK redacts binary payload in span data with this fixed substitute value
# (part of the wire protocol the integration must produce, so tests assert on
# the literal instead of importing an internal constant).
BLOB_DATA_SUBSTITUTE = "[Blob substitute]"

PYDANTIC_AI_VERSION = package_version("pydantic-ai")


@pytest.fixture
def get_test_agent():
    def inner():
        """Create a test agent with model settings."""
        return Agent(
            "test",
            name="test_agent",
            system_prompt="You are a helpful test assistant.",
        )

    return inner


@pytest.fixture
def get_test_agent_with_settings():
    def inner():
        """Create a test agent with explicit model settings."""
        from pydantic_ai import ModelSettings

        return Agent(
            "test",
            name="test_agent_settings",
            system_prompt="You are a test assistant with settings.",
            model_settings=ModelSettings(
                temperature=0.7,
                max_tokens=100,
                top_p=0.9,
            ),
        )

    return inner


@pytest.fixture
def sync_event_loop():
    # Pydantic AI creates an event loop if there is none and doesn't close it in synchronous methods.
    # Run with "-X tracemalloc=25 -W default::ResourceWarning" to reproduce.
    # https://github.com/pydantic/pydantic-ai/commit/a58dd47f9cd6494665e47bf7cf71fccbfce2c0dd
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_run_async(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that the integration creates spans for async agent runs.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    if span_streaming:
        items = capture_items("span")

        result = await test_agent.run(
            ["Message demonstrating the absence of truncation.", "Test input"]
        )

        assert result is not None
        assert result.output is not None

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[1]["name"] == "invoke_agent test_agent"
        assert spans[1]["attributes"]["sentry.origin"] == "auto.ai.pydantic_ai"

        assert spans[1]["attributes"]["sentry.op"] == "gen_ai.invoke_agent"

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # Check chat span
        chat_span = chat_spans[0]
        assert "chat" in chat_span["name"]
        assert chat_span["attributes"]["gen_ai.operation.name"] == "chat"
        assert chat_span["attributes"]["gen_ai.response.streaming"] is False
        assert json.loads(
            chat_span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        ) == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Message demonstrating the absence of truncation.",
                    },
                    {
                        "type": "text",
                        "text": "Test input",
                    },
                ],
            }
        ]
        assert "gen_ai.usage.input_tokens" in chat_span["attributes"]
        assert "gen_ai.usage.output_tokens" in chat_span["attributes"]
    elif stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        result = await test_agent.run(
            ["Message demonstrating the absence of truncation.", "Test input"]
        )

        assert result is not None
        assert result.output is not None

        (transaction,) = (item.payload for item in items if item.type == "transaction")

        # Verify transaction (the transaction IS the invoke_agent span)
        assert transaction["transaction"] == "invoke_agent test_agent"
        assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

        # The transaction itself should have invoke_agent data
        assert transaction["contexts"]["trace"]["op"] == "gen_ai.invoke_agent"

        spans = [item.payload for item in items if item.type == "span"]
        # Find child span types (invoke_agent is the transaction, not a child span)
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # Check chat span
        chat_span = chat_spans[0]
        assert "chat" in chat_span["name"]
        assert chat_span["attributes"]["gen_ai.operation.name"] == "chat"
        assert chat_span["attributes"]["gen_ai.response.streaming"] is False
        assert json.loads(
            chat_span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        ) == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Message demonstrating the absence of truncation.",
                    },
                    {
                        "type": "text",
                        "text": "Test input",
                    },
                ],
            }
        ]
        assert "gen_ai.usage.input_tokens" in chat_span["attributes"]
        assert "gen_ai.usage.output_tokens" in chat_span["attributes"]
    else:
        events = capture_events()

        result = await test_agent.run("Test input")

        assert result is not None
        assert result.output is not None

        (transaction,) = events
        spans = transaction["spans"]

        # Verify transaction (the transaction IS the invoke_agent span)
        assert transaction["transaction"] == "invoke_agent test_agent"
        assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

        # The transaction itself should have invoke_agent data
        assert transaction["contexts"]["trace"]["op"] == "gen_ai.invoke_agent"

        # Find child span types (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        # Check chat span
        chat_span = chat_spans[0]
        assert "chat" in chat_span["description"]
        assert chat_span["data"]["gen_ai.operation.name"] == "chat"
        assert chat_span["data"]["gen_ai.response.streaming"] is False
        assert "gen_ai.request.messages" in chat_span["data"]
        assert "gen_ai.usage.input_tokens" in chat_span["data"]
        assert "gen_ai.usage.output_tokens" in chat_span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_run_async_model_error(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    def failing_model(messages, info):
        raise RuntimeError("model exploded")

    agent = Agent(
        FunctionModel(failing_model),
        name="test_agent",
    )

    if span_streaming:
        items = capture_items("event", "span")

        with pytest.raises(RuntimeError, match="model exploded"):
            await agent.run("Test input")

        (error,) = (item.payload for item in items if item.type == "event")
        assert error["level"] == "error"

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 2

        assert spans[0]["status"] == "error"
    elif stream_gen_ai_spans:
        items = capture_items("event", "span")

        with pytest.raises(RuntimeError, match="model exploded"):
            await agent.run("Test input")

        (error,) = (item.payload for item in items if item.type == "event")
        assert error["level"] == "error"

        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 1

        assert spans[0]["status"] == "error"
    else:
        events = capture_events()

        with pytest.raises(RuntimeError, match="model exploded"):
            await agent.run("Test input")

        (error, transaction) = events
        assert error["level"] == "error"

        spans = transaction["spans"]
        assert len(spans) == 1

        assert spans[0]["status"] == "internal_error"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_agent_run_sync(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
    sync_event_loop,
):
    """
    Test that the integration creates spans for sync agent runs.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    if span_streaming:
        items = capture_items("span")

        result = test_agent.run_sync(
            ["Message demonstrating the absence of truncation.", "Test input"]
        )

        assert result is not None
        assert result.output is not None

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[1]["name"] == "invoke_agent test_agent"
        assert spans[1]["attributes"]["sentry.origin"] == "auto.ai.pydantic_ai"

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # Verify streaming flag is False for sync
        assert chat_spans[0]["attributes"]["gen_ai.response.streaming"] is False
    elif stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        result = test_agent.run_sync(
            ["Message demonstrating the absence of truncation.", "Test input"]
        )

        assert result is not None
        assert result.output is not None

        # Verify transaction
        (transaction,) = (item.payload for item in items if item.type == "transaction")

        # Verify transaction
        assert transaction["transaction"] == "invoke_agent test_agent"
        assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

        # Find span types
        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # Verify streaming flag is False for sync
        assert chat_spans[0]["attributes"]["gen_ai.response.streaming"] is False
    else:
        events = capture_events()

        result = test_agent.run_sync("Test input")

        assert result is not None
        assert result.output is not None

        (transaction,) = events
        spans = transaction["spans"]

        # Verify transaction
        assert transaction["transaction"] == "invoke_agent test_agent"
        assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

        # Find span types
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        # Verify streaming flag is False for sync
        assert chat_spans[0]["data"]["gen_ai.response.streaming"] is False


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_agent_run_sync_model_error(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
    sync_event_loop,
):
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    def failing_model(messages, info):
        raise RuntimeError("model exploded")

    agent = Agent(
        FunctionModel(failing_model),
        name="test_agent",
    )

    if span_streaming:
        items = capture_items("event", "span")

        with pytest.raises(RuntimeError, match="model exploded"):
            agent.run_sync("Test input")

        (error,) = (item.payload for item in items if item.type == "event")
        assert error["level"] == "error"

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 2

        assert spans[0]["status"] == "error"
    elif stream_gen_ai_spans:
        items = capture_items("event", "span")

        with pytest.raises(RuntimeError, match="model exploded"):
            agent.run_sync("Test input")

        (error,) = (item.payload for item in items if item.type == "event")
        assert error["level"] == "error"

        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 1

        assert spans[0]["status"] == "error"
    else:
        events = capture_events()

        with pytest.raises(RuntimeError, match="model exploded"):
            agent.run_sync("Test input")

        (error, transaction) = events
        assert error["level"] == "error"

        spans = transaction["spans"]
        assert len(spans) == 1

        assert spans[0]["status"] == "internal_error"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_run_stream(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that the integration creates spans for streaming agent runs.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    if span_streaming:
        items = capture_items("span")

        async with test_agent.run_stream(
            ["Message demonstrating the absence of truncation.", "Test input"]
        ) as result:
            # Consume the stream
            async for _ in result.stream_output():
                pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[1]["name"] == "invoke_agent test_agent"
        assert spans[1]["attributes"]["sentry.origin"] == "auto.ai.pydantic_ai"

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # Verify streaming flag is True for streaming
        assert chat_spans[0]["attributes"]["gen_ai.response.streaming"] is True
        assert json.loads(
            chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        ) == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Message demonstrating the absence of truncation.",
                    },
                    {
                        "type": "text",
                        "text": "Test input",
                    },
                ],
            }
        ]
        assert "gen_ai.usage.input_tokens" in chat_spans[0]["attributes"]
        # Streaming responses should still have output data
        assert (
            "gen_ai.response.text" in chat_spans[0]["attributes"]
            or "gen_ai.response.model" in chat_spans[0]["attributes"]
        )
    elif stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        async with test_agent.run_stream(
            ["Message demonstrating the absence of truncation.", "Test input"]
        ) as result:
            # Consume the stream
            async for _ in result.stream_output():
                pass

        # Verify transaction
        (transaction,) = (item.payload for item in items if item.type == "transaction")

        # Verify transaction
        assert transaction["transaction"] == "invoke_agent test_agent"
        assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

        # Find chat spans
        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # Verify streaming flag is True for streaming
        assert chat_spans[0]["attributes"]["gen_ai.response.streaming"] is True
        assert json.loads(
            chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        ) == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Message demonstrating the absence of truncation.",
                    },
                    {
                        "type": "text",
                        "text": "Test input",
                    },
                ],
            }
        ]
        assert "gen_ai.usage.input_tokens" in chat_spans[0]["attributes"]
        # Streaming responses should still have output data
        assert (
            "gen_ai.response.text" in chat_spans[0]["attributes"]
            or "gen_ai.response.model" in chat_spans[0]["attributes"]
        )
    else:
        events = capture_events()

        async with test_agent.run_stream("Test input") as result:
            # Consume the stream
            async for _ in result.stream_output():
                pass

        (transaction,) = events
        spans = transaction["spans"]

        # Verify transaction
        assert transaction["transaction"] == "invoke_agent test_agent"
        assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

        # Find chat spans
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        # Verify streaming flag is True for streaming
        assert chat_spans[0]["data"]["gen_ai.response.streaming"] is True
        assert "gen_ai.request.messages" in chat_spans[0]["data"]
        assert "gen_ai.usage.input_tokens" in chat_spans[0]["data"]
        # Streaming responses should still have output data
        assert (
            "gen_ai.response.text" in chat_spans[0]["data"]
            or "gen_ai.response.model" in chat_spans[0]["data"]
        )


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_run_stream_events(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that run_stream_events creates spans (it uses run internally, so non-streaming).
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    # Consume all events
    test_agent = get_test_agent()

    if span_streaming:
        items = capture_items("span")

        if PYDANTIC_AI_VERSION > (2,):
            async with test_agent.run_stream_events(
                ["Message demonstrating the absence of truncation.", "Test input"]
            ) as stream_events:
                async for _ in stream_events:
                    pass
        else:
            async for _ in test_agent.run_stream_events(
                ["Message demonstrating the absence of truncation.", "Test input"]
            ):
                pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[-1]["name"] == "invoke_agent test_agent"

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # run_stream_events uses run() internally, so streaming should be False
        assert chat_spans[0]["attributes"]["gen_ai.response.streaming"] is False
    elif stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        if PYDANTIC_AI_VERSION > (2,):
            async with test_agent.run_stream_events(
                ["Message demonstrating the absence of truncation.", "Test input"]
            ) as stream_events:
                async for _ in stream_events:
                    pass
        else:
            async for _ in test_agent.run_stream_events(
                ["Message demonstrating the absence of truncation.", "Test input"]
            ):
                pass

        # Verify transaction
        (transaction,) = (item.payload for item in items if item.type == "transaction")

        # Verify transaction
        assert transaction["transaction"] == "invoke_agent test_agent"

        # Find chat spans
        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        # run_stream_events uses run() internally, so streaming should be False
        assert chat_spans[0]["attributes"]["gen_ai.response.streaming"] is False
    else:
        events = capture_events()

        if PYDANTIC_AI_VERSION > (2,):
            async with test_agent.run_stream_events("Test input") as stream_events:
                async for _ in stream_events:
                    pass
        else:
            async for _ in test_agent.run_stream_events("Test input"):
                pass

        (transaction,) = events

        # Verify transaction
        assert transaction["transaction"] == "invoke_agent test_agent"

        # Find chat spans
        spans = transaction["spans"]
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        # run_stream_events uses run() internally, so streaming should be False
        assert chat_spans[0]["data"]["gen_ai.response.streaming"] is False


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_with_tools(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that tool execution creates execute_tool spans.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def add_numbers(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        result = await test_agent.run("What is 5 + 3?")

        assert result is not None

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find child span types (invoke_agent is the transaction, not a child span)
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        tool_spans = [
            s
            for s in spans
            if s["attributes"].get("sentry.op", "") == "gen_ai.execute_tool"
        ]

        # Should have tool spans
        assert len(tool_spans) >= 1

        # Check tool span
        tool_span = tool_spans[0]
        assert "execute_tool" in tool_span["name"]
        assert tool_span["attributes"]["gen_ai.operation.name"] == "execute_tool"
        assert tool_span["attributes"]["gen_ai.tool.name"] == "add_numbers"
        assert "gen_ai.tool.input" in tool_span["attributes"]
        assert "gen_ai.tool.output" in tool_span["attributes"]

        # Check chat spans have available_tools
        for chat_span in chat_spans:
            assert "gen_ai.request.available_tools" in chat_span["attributes"]
            available_tools_str = chat_span["attributes"][
                "gen_ai.request.available_tools"
            ]
            # Available tools is serialized as a string
            assert "add_numbers" in available_tools_str
    else:
        events = capture_events()

        result = await test_agent.run("What is 5 + 3?")

        assert result is not None

        (transaction,) = events
        spans = transaction["spans"]

        # Find child span types (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        tool_spans = [s for s in spans if s["op"] == "gen_ai.execute_tool"]

        # Should have tool spans
        assert len(tool_spans) >= 1

        # Check tool span
        tool_span = tool_spans[0]
        assert "execute_tool" in tool_span["description"]
        assert tool_span["data"]["gen_ai.operation.name"] == "execute_tool"
        assert tool_span["data"]["gen_ai.tool.name"] == "add_numbers"
        assert "gen_ai.tool.input" in tool_span["data"]
        assert "gen_ai.tool.output" in tool_span["data"]

        # Check chat spans have available_tools
        for chat_span in chat_spans:
            assert "gen_ai.request.available_tools" in chat_span["data"]
            available_tools_str = chat_span["data"]["gen_ai.request.available_tools"]
            # Available tools is serialized as a string
            assert "add_numbers" in available_tools_str


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "handled_tool_call_exceptions",
    [False, True],
)
@pytest.mark.asyncio
async def test_agent_with_tool_model_retry(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    handled_tool_call_exceptions,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that a handled exception is captured when a tool raises ModelRetry.
    """
    sentry_init(
        integrations=[
            PydanticAIIntegration(
                handled_tool_call_exceptions=handled_tool_call_exceptions
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    retries = 0

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def add_numbers(a: int, b: int) -> float:
        """Add two numbers together, but raises an exception on the first attempt."""
        nonlocal retries
        if retries == 0:
            retries += 1
            raise ModelRetry(message="Try again with the same arguments.")
        return a + b

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("event", "span")

        result = await test_agent.run("What is 5 + 3?")

        assert result is not None

        if handled_tool_call_exceptions:
            (error,) = (item.payload for item in items if item.type == "event")
            assert error["level"] == "error"
            assert error["exception"]["values"][0]["mechanism"]["handled"]

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        # Find child span types (invoke_agent is the transaction, not a child span)
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        tool_spans = [
            s
            for s in spans
            if s["attributes"].get("sentry.op", "") == "gen_ai.execute_tool"
        ]

        # Should have tool spans
        assert len(tool_spans) >= 1

        # Check tool spans
        model_retry_tool_span = tool_spans[0]
        assert "execute_tool" in model_retry_tool_span["name"]
        assert (
            model_retry_tool_span["attributes"]["gen_ai.operation.name"]
            == "execute_tool"
        )
        assert model_retry_tool_span["attributes"]["gen_ai.tool.name"] == "add_numbers"
        assert "gen_ai.tool.input" in model_retry_tool_span["attributes"]

        tool_span = tool_spans[1]
        assert "execute_tool" in tool_span["name"]
        assert tool_span["attributes"]["gen_ai.operation.name"] == "execute_tool"
        assert tool_span["attributes"]["gen_ai.tool.name"] == "add_numbers"
        assert "gen_ai.tool.input" in tool_span["attributes"]
        assert "gen_ai.tool.output" in tool_span["attributes"]

        # Check chat spans have available_tools
        for chat_span in chat_spans:
            assert "gen_ai.request.available_tools" in chat_span["attributes"]
            available_tools_str = chat_span["attributes"][
                "gen_ai.request.available_tools"
            ]

            # Available tools is serialized as a string
            assert "add_numbers" in available_tools_str
    else:
        events = capture_events()

        result = await test_agent.run("What is 5 + 3?")

        assert result is not None

        if handled_tool_call_exceptions:
            (error, transaction) = events
        else:
            (transaction,) = events
        spans = transaction["spans"]

        if handled_tool_call_exceptions:
            assert error["level"] == "error"
            assert error["exception"]["values"][0]["mechanism"]["handled"]

        # Find child span types (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        tool_spans = [s for s in spans if s["op"] == "gen_ai.execute_tool"]

        # Should have tool spans
        assert len(tool_spans) >= 1

        # Check tool spans
        model_retry_tool_span = tool_spans[0]
        assert "execute_tool" in model_retry_tool_span["description"]
        assert model_retry_tool_span["data"]["gen_ai.operation.name"] == "execute_tool"
        assert model_retry_tool_span["data"]["gen_ai.tool.name"] == "add_numbers"
        assert "gen_ai.tool.input" in model_retry_tool_span["data"]

        tool_span = tool_spans[1]
        assert "execute_tool" in tool_span["description"]
        assert tool_span["data"]["gen_ai.operation.name"] == "execute_tool"
        assert tool_span["data"]["gen_ai.tool.name"] == "add_numbers"
        assert "gen_ai.tool.input" in tool_span["data"]
        assert "gen_ai.tool.output" in tool_span["data"]

        # Check chat spans have available_tools
        for chat_span in chat_spans:
            assert "gen_ai.request.available_tools" in chat_span["data"]
            available_tools_str = chat_span["data"]["gen_ai.request.available_tools"]
            # Available tools is serialized as a string
            assert "add_numbers" in available_tools_str


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "handled_tool_call_exceptions",
    [False, True],
)
@pytest.mark.asyncio
async def test_agent_with_tool_validation_error(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    handled_tool_call_exceptions,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that a handled exception is captured when a tool has unsatisfiable constraints.
    """
    sentry_init(
        integrations=[
            PydanticAIIntegration(
                handled_tool_call_exceptions=handled_tool_call_exceptions
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def add_numbers(a: Annotated[int, Field(gt=0, lt=0)], b: int) -> int:
        """Add two numbers together."""
        return a + b

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("event", "span")

        result = None
        with pytest.raises(UnexpectedModelBehavior):
            result = await test_agent.run("What is 5 + 3?")

        assert result is None

        if handled_tool_call_exceptions:
            (
                error,
                model_behaviour_error,
            ) = (item.payload for item in items if item.type == "event")

            assert error["level"] == "error"
            assert error["exception"]["values"][0]["mechanism"]["handled"]

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        # Check chat spans have available_tools
        assert "gen_ai.request.available_tools" in chat_spans[0]["attributes"]
        available_tools_str = chat_spans[0]["attributes"][
            "gen_ai.request.available_tools"
        ]

        # Available tools is serialized as a string
        assert "add_numbers" in available_tools_str
    else:
        events = capture_events()

        result = None
        with pytest.raises(UnexpectedModelBehavior):
            result = await test_agent.run("What is 5 + 3?")

        assert result is None

        if handled_tool_call_exceptions:
            (error, model_behaviour_error, transaction) = events
        else:
            (
                model_behaviour_error,
                transaction,
            ) = events
        spans = transaction["spans"]

        if handled_tool_call_exceptions:
            assert error["level"] == "error"
            assert error["exception"]["values"][0]["mechanism"]["handled"]

        # Find child span types (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        # Check chat spans have available_tools
        assert "gen_ai.request.available_tools" in chat_spans[0]["data"]
        available_tools_str = chat_spans[0]["data"]["gen_ai.request.available_tools"]
        # Available tools is serialized as a string
        assert "add_numbers" in available_tools_str


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_with_tools_streaming(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that tool execution works correctly with streaming.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        async with test_agent.run_stream("What is 7 times 8?") as result:
            async for _ in result.stream_output():
                pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find span types
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        tool_spans = [
            s
            for s in spans
            if s["attributes"].get("sentry.op", "") == "gen_ai.execute_tool"
        ]

        # Should have tool spans
        assert len(tool_spans) >= 1

        # Verify streaming flag is True
        assert chat_spans[0]["attributes"]["gen_ai.response.streaming"] is True

        # Check tool span
        tool_span = tool_spans[0]
        assert tool_span["attributes"]["gen_ai.tool.name"] == "multiply"
        assert "gen_ai.tool.input" in tool_span["attributes"]
        assert "gen_ai.tool.output" in tool_span["attributes"]
    else:
        events = capture_events()

        async with test_agent.run_stream("What is 7 times 8?") as result:
            async for _ in result.stream_output():
                pass

        (transaction,) = events
        spans = transaction["spans"]

        # Find span types
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        tool_spans = [s for s in spans if s["op"] == "gen_ai.execute_tool"]

        # Should have tool spans
        assert len(tool_spans) >= 1

        # Verify streaming flag is True
        assert chat_spans[0]["data"]["gen_ai.response.streaming"] is True

        # Check tool span
        tool_span = tool_spans[0]
        assert tool_span["data"]["gen_ai.tool.name"] == "multiply"
        assert "gen_ai.tool.input" in tool_span["data"]
        assert "gen_ai.tool.output" in tool_span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_model_settings(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent_with_settings,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that model settings are captured in spans.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent_with_settings = get_test_agent_with_settings()

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent_with_settings.run("Test input")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find chat span
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]
        # Check that model settings are captured
        assert chat_span["attributes"].get("gen_ai.request.temperature") == 0.7
        assert chat_span["attributes"].get("gen_ai.request.max_tokens") == 100
        assert chat_span["attributes"].get("gen_ai.request.top_p") == 0.9
    else:
        events = capture_events()

        await test_agent_with_settings.run("Test input")

        (transaction,) = events
        spans = transaction["spans"]

        # Find chat span
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]
        # Check that model settings are captured
        assert chat_span["data"].get("gen_ai.request.temperature") == 0.7
        assert chat_span["data"].get("gen_ai.request.max_tokens") == 100
        assert chat_span["data"].get("gen_ai.request.top_p") == 0.9


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_system_prompt_attribute(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that system prompts are included as the first message.
    """
    agent = Agent(
        "test",
        name="test_system",
        system_prompt="You are a helpful assistant specialized in testing.",
    )

    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await agent.run("Hello")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # The transaction IS the invoke_agent span, check for messages in chat spans instead
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]

        if send_default_pii and include_prompts:
            system_instructions = chat_span["attributes"][
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS
            ]
            assert json.loads(system_instructions) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant specialized in testing.",
                }
            ]
        else:
            assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_span["attributes"]
    else:
        events = capture_events()

        await agent.run("Hello")

        (transaction,) = events
        spans = transaction["spans"]

        # The transaction IS the invoke_agent span, check for messages in chat spans instead
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]

        if send_default_pii and include_prompts:
            system_instructions = chat_span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            assert json.loads(system_instructions) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant specialized in testing.",
                }
            ]
        else:
            assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_error_handling(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test error handling in agent execution.
    """
    # Use a simpler test that doesn't cause tool failures
    # as pydantic-ai has complex error handling for tool errors
    agent = Agent(
        "test",
        name="test_error",
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")

        # Simple run that should succeed
        await agent.run("Hello")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[1]["is_segment"] is True
        assert spans[1]["status"] != "error"  # Could be None or some other status
    elif stream_gen_ai_spans:
        items = capture_items("transaction")

        # Simple run that should succeed
        await agent.run("Hello")

        # At minimum, we should have a transaction
        transaction = next(item.payload for item in items)

        assert transaction["transaction"] == "invoke_agent test_error"
        # Transaction should complete successfully (status key may not exist if no error)
        trace_status = transaction["contexts"]["trace"].get("status")
        assert trace_status != "error"  # Could be None or some other status
    else:
        events = capture_events()

        # Simple run that should succeed
        await agent.run("Hello")

        # At minimum, we should have a transaction
        assert len(events) == 1
        transaction = [e for e in events if e.get("type") == "transaction"][0]

        assert transaction["transaction"] == "invoke_agent test_error"
        # Transaction should complete successfully (status key may not exist if no error)
        trace_status = transaction["contexts"]["trace"].get("status")
        assert trace_status != "error"  # Could be None or some other status


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_without_pii(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that PII is not captured when send_default_pii is False.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        test_agent = get_test_agent()
        await test_agent.run("Sensitive input")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        # Verify that messages and response text are not captured
        for span in chat_spans:
            assert "gen_ai.request.messages" not in span["attributes"]
            assert "gen_ai.response.text" not in span["attributes"]
    else:
        events = capture_events()

        test_agent = get_test_agent()
        await test_agent.run("Sensitive input")

        (transaction,) = events
        spans = transaction["spans"]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        # Verify that messages and response text are not captured
        for span in chat_spans:
            assert "gen_ai.request.messages" not in span["data"]
            assert "gen_ai.response.text" not in span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_without_pii_tools(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that tool input/output are not captured when send_default_pii is False.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def sensitive_tool(data: str) -> str:
        """A tool with sensitive data."""
        return f"Processed: {data}"

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Use sensitive tool with private data")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find tool spans
        tool_spans = [
            s
            for s in spans
            if s["attributes"].get("sentry.op", "") == "gen_ai.execute_tool"
        ]

        # If tool was executed, verify input/output are not captured
        for tool_span in tool_spans:
            assert "gen_ai.tool.input" not in tool_span["attributes"]
            assert "gen_ai.tool.output" not in tool_span["attributes"]
    else:
        events = capture_events()

        await test_agent.run("Use sensitive tool with private data")

        (transaction,) = events
        spans = transaction["spans"]

        # Find tool spans
        tool_spans = [s for s in spans if s["op"] == "gen_ai.execute_tool"]

        # If tool was executed, verify input/output are not captured
        for tool_span in tool_spans:
            assert "gen_ai.tool.input" not in tool_span["data"]
            assert "gen_ai.tool.output" not in tool_span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_multiple_agents_concurrent(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that multiple agents can run concurrently without interfering.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    async def run_agent(input_text):
        return await test_agent.run(input_text)

    if span_streaming:
        items = capture_items("span")

        # Run 3 agents concurrently
        results = await asyncio.gather(*[run_agent(f"Input {i}") for i in range(3)])

        assert len(results) == 3

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        for span in spans:
            if span["is_segment"] is False:
                continue
            assert span["name"] == "invoke_agent test_agent"
    elif stream_gen_ai_spans:
        items = capture_items("transaction")

        # Run 3 agents concurrently
        results = await asyncio.gather(*[run_agent(f"Input {i}") for i in range(3)])

        assert len(results) == 3

        # Verify each transaction is separate
        events = [item.payload for item in items]
        assert len(events) == 3
        for i, transaction in enumerate(events):
            assert transaction["transaction"] == "invoke_agent test_agent"
    else:
        events = capture_events()

        # Run 3 agents concurrently
        results = await asyncio.gather(*[run_agent(f"Input {i}") for i in range(3)])

        assert len(results) == 3
        assert len(events) == 3

        # Verify each transaction is separate
        for i, transaction in enumerate(events):
            assert transaction["type"] == "transaction"
            assert transaction["transaction"] == "invoke_agent test_agent"
            # Each should have its own spans
            assert len(transaction["spans"]) == 1


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_message_history(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that full conversation history is captured in chat spans.
    """
    agent = Agent(
        "test",
        name="test_history",
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    # Second message with history
    from pydantic_ai import messages

    history = [
        messages.ModelRequest(
            parts=[messages.UserPromptPart(content="Hello, I'm Alice")]
        ),
        messages.ModelResponse(
            parts=[messages.TextPart(content="Hello Alice! How can I help you?")],
            model_name="test",
        ),
    ]

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        # First message
        await agent.run("Hello, I'm Alice")

        await agent.run("What is my name?", message_history=history)

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        if chat_spans:
            chat_span = chat_spans[0]
            if "gen_ai.request.messages" in chat_span["attributes"]:
                messages_data = chat_span["attributes"]["gen_ai.request.messages"]
                # Should have multiple messages including history
                assert len(messages_data) > 1
    else:
        events = capture_events()

        # First message
        await agent.run("Hello, I'm Alice")

        await agent.run("What is my name?", message_history=history)

        # We should have 2 transactions
        assert len(events) == 2

        # Check the second transaction has the full history
        second_transaction = events[1]
        spans = second_transaction["spans"]
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        if chat_spans:
            chat_span = chat_spans[0]
            if "gen_ai.request.messages" in chat_span["data"]:
                messages_data = chat_span["data"]["gen_ai.request.messages"]
                # Should have multiple messages including history
                assert len(messages_data) > 1


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_gen_ai_system(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that gen_ai.system is set from the model.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Test input")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find chat span
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]
        # gen_ai.system should be set from the model (TestModel -> 'test')
        assert "gen_ai.system" in chat_span["attributes"]
        assert chat_span["attributes"]["gen_ai.system"] == "test"
    else:
        events = capture_events()

        await test_agent.run("Test input")

        (transaction,) = events
        spans = transaction["spans"]

        # Find chat span
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]
        # gen_ai.system should be set from the model (TestModel -> 'test')
        assert "gen_ai.system" in chat_span["data"]
        assert chat_span["data"]["gen_ai.system"] == "test"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_include_prompts_false(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that prompts are not captured when include_prompts=False.
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,  # Even with PII enabled, prompts should not be captured
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Sensitive prompt")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        # Verify that messages and response text are not captured
        for span in chat_spans:
            assert "gen_ai.request.messages" not in span["attributes"]
            assert "gen_ai.response.text" not in span["attributes"]
    else:
        events = capture_events()

        await test_agent.run("Sensitive prompt")

        (transaction,) = events
        spans = transaction["spans"]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        # Verify that messages and response text are not captured
        for span in chat_spans:
            assert "gen_ai.request.messages" not in span["data"]
            assert "gen_ai.response.text" not in span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_include_prompts_true(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that prompts are captured when include_prompts=True (default).
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Test prompt")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        # Verify that messages are captured in chat spans
        assert len(chat_spans) == 1
        assert "gen_ai.request.messages" in chat_spans[0]["attributes"]
    else:
        events = capture_events()

        await test_agent.run("Test prompt")

        (transaction,) = events
        spans = transaction["spans"]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        # Verify that messages are captured in chat spans
        assert len(chat_spans) == 1
        assert "gen_ai.request.messages" in chat_spans[0]["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_include_prompts_false_with_tools(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that tool input/output are not captured when include_prompts=False.
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def test_tool(value: int) -> int:
        """A test tool."""
        return value * 2

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Use the test tool with value 5")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find tool spans
        tool_spans = [
            s
            for s in spans
            if s["attributes"].get("sentry.op", "") == "gen_ai.execute_tool"
        ]

        # If tool was executed, verify input/output are not captured
        for tool_span in tool_spans:
            assert "gen_ai.tool.input" not in tool_span["attributes"]
            assert "gen_ai.tool.output" not in tool_span["attributes"]
    else:
        events = capture_events()

        await test_agent.run("Use the test tool with value 5")

        (transaction,) = events
        spans = transaction["spans"]

        # Find tool spans
        tool_spans = [s for s in spans if s["op"] == "gen_ai.execute_tool"]

        # If tool was executed, verify input/output are not captured
        for tool_span in tool_spans:
            assert "gen_ai.tool.input" not in tool_span["data"]
            assert "gen_ai.tool.output" not in tool_span["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_include_prompts_requires_pii(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that include_prompts requires send_default_pii=True.
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=False,  # PII disabled
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Test prompt")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        # Even with include_prompts=True, if PII is disabled, messages should not be captured
        for span in chat_spans:
            assert "gen_ai.request.messages" not in span["attributes"]
            assert "gen_ai.response.text" not in span["attributes"]
    else:
        events = capture_events()

        await test_agent.run("Test prompt")

        (transaction,) = events
        spans = transaction["spans"]

        # Find child spans (invoke_agent is the transaction, not a child span)
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        # Even with include_prompts=True, if PII is disabled, messages should not be captured
        for span in chat_spans:
            assert "gen_ai.request.messages" not in span["data"]
            assert "gen_ai.response.text" not in span["data"]


@pytest.mark.asyncio
async def test_context_cleanup_after_run(sentry_init, get_test_agent):
    """
    Test that the pydantic_ai_agent context is properly cleaned up after agent execution.
    """

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Verify no agent context is set before the run
    assert get_current_agent() is None

    # Run the agent
    test_agent = get_test_agent()
    await test_agent.run("Test input")

    # Verify the agent context is cleaned up after the run
    assert get_current_agent() is None


def test_context_cleanup_after_run_sync(sentry_init, get_test_agent, sync_event_loop):
    """
    Test that the pydantic_ai_agent context is properly cleaned up after sync agent execution.
    """

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Verify no agent context is set before the run
    assert get_current_agent() is None

    # Run the agent synchronously
    test_agent = get_test_agent()
    test_agent.run_sync("Test input")

    # Verify the agent context is cleaned up after the run
    assert get_current_agent() is None


@pytest.mark.asyncio
async def test_context_cleanup_after_streaming(sentry_init, get_test_agent):
    """
    Test that the pydantic_ai_agent context is properly cleaned up after streaming execution.
    """

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Verify no agent context is set before the run
    assert get_current_agent() is None

    test_agent = get_test_agent()
    # Run the agent with streaming
    async with test_agent.run_stream("Test input") as result:
        async for _ in result.stream_output():
            pass

    # Verify the agent context is cleaned up once streaming completes
    assert get_current_agent() is None


@pytest.mark.asyncio
async def test_context_cleanup_on_error(sentry_init, get_test_agent):
    """
    Test that the pydantic_ai_agent context is cleaned up even when an error occurs.
    """

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    test_agent = get_test_agent()

    # Create an agent with a tool that raises an error
    @test_agent.tool_plain
    def failing_tool() -> str:
        """A tool that always fails."""
        raise ValueError("Tool error")

    # Verify no agent context is set before the run
    assert get_current_agent() is None

    # Run the agent - this may or may not raise depending on pydantic-ai's error handling
    try:
        await test_agent.run("Use the failing tool")
    except Exception:
        pass

    # Verify the agent context is cleaned up even if there was an error
    assert get_current_agent() is None


@pytest.mark.asyncio
async def test_context_isolation_concurrent_agents(sentry_init, get_test_agent):
    """
    Test that concurrent agent executions maintain isolated contexts.
    """

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Create a second agent
    agent2 = Agent(
        "test",
        name="test_agent_2",
        system_prompt="Second test agent.",
    )

    async def run_and_check_context(agent, agent_name):
        """Run an agent and verify its context during and after execution."""
        # Run the agent
        await agent.run(f"Input for {agent_name}")

        # After execution, the agent context should be cleaned up
        assert get_current_agent() is None

        return agent_name

    test_agent = get_test_agent()
    # Run both agents concurrently
    results = await asyncio.gather(
        run_and_check_context(test_agent, "agent1"),
        run_and_check_context(agent2, "agent2"),
    )

    assert results == ["agent1", "agent2"]

    # Final check: no agent context should remain
    assert get_current_agent() is None


# ==================== Additional Coverage Tests ====================


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_invoke_agent_with_list_user_prompt(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that invoke_agent span handles list user prompts correctly.
    """
    agent = Agent(
        "test",
        name="test_list_prompt",
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")

        # Use a list as user prompt
        await agent.run(["First part", "Second part"])

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        if "gen_ai.request.messages" in spans[0]["attributes"]:
            messages_str = spans[0]["attributes"]["gen_ai.request.messages"]
            assert "First part" in messages_str
            assert "Second part" in messages_str
    elif stream_gen_ai_spans:
        items = capture_items("transaction")

        # Use a list as user prompt
        await agent.run(["First part", "Second part"])

        (transaction,) = [item.payload for item in items]

        # Check that the invoke_agent transaction has messages data
        # The invoke_agent is the transaction itself
        if "gen_ai.request.messages" in transaction["contexts"]["trace"]["data"]:
            messages_str = transaction["contexts"]["trace"]["data"][
                "gen_ai.request.messages"
            ]
            assert "First part" in messages_str
            assert "Second part" in messages_str
    else:
        events = capture_events()

        # Use a list as user prompt
        await agent.run(["First part", "Second part"])

        (transaction,) = events

        # Check that the invoke_agent transaction has messages data
        # The invoke_agent is the transaction itself
        if "gen_ai.request.messages" in transaction["contexts"]["trace"]["data"]:
            messages_str = transaction["contexts"]["trace"]["data"][
                "gen_ai.request.messages"
            ]
            assert "First part" in messages_str
            assert "Second part" in messages_str


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_invoke_agent_with_instructions(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that invoke_agent span handles instructions correctly.
    """
    from pydantic_ai import Agent

    agent = Agent(
        "test",
        name="test_instructions",
        instructions=["Instruction 1", "Instruction 2"],
        system_prompt="System prompt",
    )

    # pydantic-ai >=2.36.0 joins multiple instructions with a blank line, earlier versions with a single newline
    instructions_separator = "\n\n" if PYDANTIC_AI_VERSION >= (2, 36) else "\n"

    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await agent.run("Test input")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # The transaction IS the invoke_agent span, check for messages in chat spans instead
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]

        if send_default_pii and include_prompts:
            system_instructions = chat_span["attributes"][
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS
            ]
            assert json.loads(system_instructions) == [
                {"type": "text", "content": "System prompt"},
                {
                    "type": "text",
                    "content": f"Instruction 1{instructions_separator}Instruction 2",
                },
            ]
        else:
            assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_span["attributes"]

    else:
        events = capture_events()

        await agent.run("Test input")

        (transaction,) = events
        spans = transaction["spans"]

        # The transaction IS the invoke_agent span, check for messages in chat spans instead
        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]

        if send_default_pii and include_prompts:
            system_instructions = chat_span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            assert json.loads(system_instructions) == [
                {"type": "text", "content": "System prompt"},
                {
                    "type": "text",
                    "content": f"Instruction 1{instructions_separator}Instruction 2",
                },
            ]
        else:
            assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_span["data"]


@pytest.mark.asyncio
async def test_model_name_extraction_with_callable(sentry_init, capture_items):
    """
    Test that the chat span reports the model name of a model exposing a
    callable name() attribute.
    """
    from unittest.mock import MagicMock

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    mock_model = MagicMock()
    # Remove model_name attribute so the integration falls back to name()
    del mock_model.model_name
    mock_model.name = lambda: "custom-model-name"

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([], None, mock_model, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    assert (
        chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
        == "custom-model-name"
    )


@pytest.mark.asyncio
async def test_model_name_extraction_fallback_to_str(sentry_init, capture_items):
    """
    Test the chat span falls back to str(model) when no name attribute exists.
    """
    from unittest.mock import MagicMock

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    mock_model = MagicMock()
    # Remove name and model_name attributes
    del mock_model.name
    del mock_model.model_name

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([], None, mock_model, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    # Should report the string representation of the model object
    model = chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
    assert isinstance(model, str)
    assert model == str(mock_model)


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_usage_data_partial(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that usage data is correctly handled when only some fields are present.
    """
    agent = Agent(
        "test",
        name="test_usage",
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await agent.run("Test input")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
    else:
        events = capture_events()

        await agent.run("Test input")

        (transaction,) = events
        spans = transaction["spans"]

        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

    assert len(chat_spans) == 1

    # Check that usage data fields exist (they may or may not be set depending on TestModel)
    chat_span = chat_spans[0]
    # At minimum, the span should have been created
    assert chat_span is not None


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_data_from_scope(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that agent data can be retrieved from Sentry scope when not passed directly.
    """

    agent = Agent(
        "test",
        name="test_scope_agent",
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")

        # The integration automatically sets agent in scope during execution
        await agent.run("Test input")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[1]["name"] == "invoke_agent test_scope_agent"
    elif stream_gen_ai_spans:
        items = capture_items("transaction")

        # The integration automatically sets agent in scope during execution
        await agent.run("Test input")

        # Verify agent name is capture
        (transaction,) = (item.payload for item in items)

        # Verify agent name is captured
        assert transaction["transaction"] == "invoke_agent test_scope_agent"
    else:
        events = capture_events()

        # The integration automatically sets agent in scope during execution
        await agent.run("Test input")

        # Verify agent name is capture
        (transaction,) = events

        # Verify agent name is captured
        assert transaction["transaction"] == "invoke_agent test_scope_agent"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_available_tools_without_description(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that available tools are captured even when description is missing.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def tool_without_desc(x: int) -> int:
        # No docstring = no description
        return x * 2

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Use the tool with 5")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        if chat_spans:
            chat_span = chat_spans[0]
            if "gen_ai.request.available_tools" in chat_span["attributes"]:
                tools_str = chat_span["attributes"]["gen_ai.request.available_tools"]
                assert "tool_without_desc" in tools_str
    else:
        events = capture_events()

        await test_agent.run("Use the tool with 5")

        (transaction,) = events
        spans = transaction["spans"]

        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
        if chat_spans:
            chat_span = chat_spans[0]
            if "gen_ai.request.available_tools" in chat_span["data"]:
                tools_str = chat_span["data"]["gen_ai.request.available_tools"]
                assert "tool_without_desc" in tools_str


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_output_with_tool_calls(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that tool calls in model response are captured correctly.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def calc_tool(value: int) -> int:
        """Calculate something."""
        return value + 10

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await test_agent.run("Use calc_tool with 5")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        # At least one chat span should exist
        assert len(chat_spans) >= 1

        # Check if tool calls are captured in response
        # Tool calls may or may not be in response depending on TestModel behavior
        # Just verify the span was created and has basic data
        assert "gen_ai.operation.name" in chat_spans[0]["attributes"]
    else:
        events = capture_events()

        await test_agent.run("Use calc_tool with 5")

        (transaction,) = events
        spans = transaction["spans"]

        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        # At least one chat span should exist
        assert len(chat_spans) >= 1

        # Check if tool calls are captured in response
        # Tool calls may or may not be in response depending on TestModel behavior
        # Just verify the span was created and has basic data
        assert "gen_ai.operation.name" in chat_spans[0]["data"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_message_formatting_with_different_parts(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that different message part types are handled correctly in ai_client span.
    """
    from pydantic_ai import Agent, messages

    agent = Agent(
        "test",
        name="test_message_parts",
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    # Create message history with different part types
    history = [
        messages.ModelRequest(parts=[messages.UserPromptPart(content="Hello")]),
        messages.ModelResponse(
            parts=[
                messages.TextPart(content="Hi there!"),
            ],
            model_name="test",
        ),
    ]

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await agent.run("What did I say?", message_history=history)

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]

        # Should have chat spans
        assert len(chat_spans) == 1

        # Check that messages are captured
        chat_span = chat_spans[0]
        if "gen_ai.request.messages" in chat_span["attributes"]:
            messages_data = chat_span["attributes"]["gen_ai.request.messages"]
            assert messages_data is not None
    else:
        events = capture_events()

        await agent.run("What did I say?", message_history=history)

        (transaction,) = events
        spans = transaction["spans"]

        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

        # Should have chat spans
        assert len(chat_spans) == 1

        # Check that messages are captured
        chat_span = chat_spans[0]
        if "gen_ai.request.messages" in chat_span["data"]:
            messages_data = chat_span["data"]["gen_ai.request.messages"]
            # Should contain message history
            assert messages_data is not None


@pytest.mark.asyncio
async def test_update_invoke_agent_span_with_none_output(sentry_init, capture_items):
    """
    Test that update_invoke_agent_span handles None output gracefully.
    """
    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.invoke_agent import (
        update_invoke_agent_span,
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        span = sentry_sdk.start_span(op="test_span")

        # Update with None output - should not raise
        update_invoke_agent_span(span, None)

        span.finish()

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_update_ai_client_span_with_none_response(sentry_init, capture_items):
    """
    Test that update_ai_client_span handles None response gracefully.
    """
    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.ai_client import (
        update_ai_client_span,
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        span = sentry_sdk.start_span(op="test_span")

        # Update with None response - should not raise
        update_ai_client_span(span, None)

        span.finish()

    # Should not crash
    assert transaction is not None


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_agent_without_name(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that agent without a name is handled correctly.
    """
    # Create agent without explicit name
    agent = Agent("test")

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
        items = capture_items("span")

        await agent.run("Test input")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert "invoke_agent" in spans[1]["name"]
    elif stream_gen_ai_spans:
        items = capture_items("transaction")

        await agent.run("Test input")

        # Should still create transaction, just with default name
        (transaction,) = (item.payload for item in items)

        # Transaction name should be "invoke_agent agent" or similar default
        assert "invoke_agent" in transaction["transaction"]
    else:
        events = capture_events()

        await agent.run("Test input")

        (transaction,) = events

        # Should still create transaction, just with default name
        assert transaction["type"] == "transaction"

        # Transaction name should be "invoke_agent agent" or similar default
        assert "invoke_agent" in transaction["transaction"]


@pytest.mark.asyncio
async def test_input_messages_error_handling(sentry_init, capture_items):
    """
    Test that input message extraction handles malformed messages gracefully.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Plain objects without the expected message attributes
        invalid_messages = [object()]

        # Should not raise
        span = ai_client_span(invalid_messages, None, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0]["attributes"]
    assert transaction is not None


@pytest.mark.asyncio
async def test_available_tools_error_handling(sentry_init, capture_items):
    """
    Test that chat span creation survives agents whose toolset raises errors.
    """
    from unittest.mock import MagicMock

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Mock agent whose toolset iteration raises
        mock_agent = MagicMock()
        mock_agent.model = None
        mock_agent.model_settings = None
        mock_agent._function_toolset.tools.items.side_effect = Exception("Error")

        # Should not raise, tool extraction errors are caught internally
        span = ai_client_span([], mock_agent, None, None)
        span.finish()

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_update_ai_client_span_with_empty_usage(sentry_init, capture_items):
    """
    Test that updating the chat span with a response whose usage is None
    does not raise.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    response = ModelResponse(parts=[TextPart(content="ok")], usage=None)

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        span = ai_client_span([], None, None, None)
        update_ai_client_span(span, response)
        span.finish()

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_usage_data_partial_fields(sentry_init, capture_items):
    """
    Test that token usage from a model response is recorded on the chat span.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    usage = RequestUsage(input_tokens=100, output_tokens=25)
    response = ModelResponse(parts=[TextPart(content="ok")], usage=usage)

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([], None, None, None)
        update_ai_client_span(span, response)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    data = chat_spans[0]["attributes"]
    assert data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 100
    assert data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 25


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_message_parts_with_tool_return(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that ToolReturnPart messages are handled correctly.
    """
    from pydantic_ai import Agent

    agent = Agent(
        "test",
        name="test_tool_return",
    )

    @agent.tool_plain
    def test_tool(x: int) -> int:
        """Test tool."""
        return x * 2

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        # Run with history containing tool return
        await agent.run("Use test_tool with 5")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
    else:
        events = capture_events()

        # Run with history containing tool return
        await agent.run("Use test_tool with 5")

        (transaction,) = events
        spans = transaction["spans"]

        chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

    # Should have chat spans
    assert len(chat_spans) == 2


@pytest.mark.asyncio
async def test_message_parts_with_list_content(sentry_init, capture_items):
    """
    Test that message parts with list content are handled correctly.
    """

    import sentry_sdk

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create message with list content
        part = UserPromptPart(content=["item1", "item2", {"complex": "item"}])
        messages = [_ModelMessage([part])]

        # Should handle list content
        span = ai_client_span(messages, None, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    messages_data = _get_messages_from_span(chat_spans[0]["attributes"])
    assert any(
        content_item == {"type": "text", "text": "item1"}
        for msg in messages_data
        if isinstance(msg.get("content"), list)
        for content_item in msg["content"]
    )
    assert transaction is not None


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_output_data_transformations(
    sentry_init, capture_items, capture_events, span_streaming, stream_gen_ai_spans
):
    """
    Test transformation of the model response from `Hooks.on.after_model_request`.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    def response_model(messages, info):
        if isinstance(messages[-1].parts[-1], ToolReturnPart):
            return ModelResponse(
                parts=[
                    ThinkingPart(content="5 times 3 is 15."),
                    TextPart(content="The answer is 15."),
                ]
            )

        return ModelResponse(
            parts=[ToolCallPart(tool_name="multiply", args={"a": 5, "b": 3})]
        )

    agent = Agent(FunctionModel(response_model), name="test_agent")

    @agent.tool_plain
    def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    if span_streaming:
        items = capture_items("span")

        await agent.run("What is 5 times 3?")
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        invoke_agent_span = next(
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.invoke_agent"
        )
        assert invoke_agent_span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == (
            "The answer is 15."
        )

        chat_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.chat"
        ]
        assert json.loads(
            chat_spans[0]["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]
        ) == [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "name": "multiply",
                        "arguments": '{"a": 5, "b": 3}',
                    }
                ],
            }
        ]
        assert json.loads(
            chat_spans[1]["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]
        ) == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "reasoning", "content": "5 times 3 is 15."},
                    {"type": "text", "content": "The answer is 15."},
                ],
            }
        ]
    elif stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        await agent.run("What is 5 times 3?")

        (transaction,) = (item.payload for item in items if item.type == "transaction")
        assert transaction["contexts"]["trace"]["op"] == "gen_ai.invoke_agent"
        assert transaction["contexts"]["trace"]["data"][
            SPANDATA.GEN_AI_RESPONSE_TEXT
        ] == ("The answer is 15.")

        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.chat"
        ]
        assert json.loads(
            chat_spans[0]["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]
        ) == [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "name": "multiply",
                        "arguments": '{"a": 5, "b": 3}',
                    }
                ],
            }
        ]
        assert json.loads(
            chat_spans[1]["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]
        ) == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "reasoning", "content": "5 times 3 is 15."},
                    {"type": "text", "content": "The answer is 15."},
                ],
            }
        ]
    else:
        events = capture_events()

        await agent.run("What is 5 times 3?")

        (transaction,) = events
        assert transaction["contexts"]["trace"]["op"] == "gen_ai.invoke_agent"
        assert transaction["contexts"]["trace"]["data"][
            SPANDATA.GEN_AI_RESPONSE_TEXT
        ] == ("The answer is 15.")

        chat_spans = [
            span for span in transaction["spans"] if span["op"] == "gen_ai.chat"
        ]
        assert json.loads(chat_spans[0]["data"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "name": "multiply",
                        "arguments": '{"a": 5, "b": 3}',
                    }
                ],
            }
        ]
        assert json.loads(chat_spans[1]["data"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "reasoning", "content": "5 times 3 is 15."},
                    {"type": "text", "content": "The answer is 15."},
                ],
            }
        ]


@pytest.mark.asyncio
async def test_output_data_error_handling(sentry_init, capture_items):
    """
    Test that response output extraction handles malformed responses gracefully.
    """
    from unittest.mock import MagicMock

    import sentry_sdk

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    items = capture_items()

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        span = ai_client_span([], None, None, None)

        # Create mock response whose parts raise when formatted
        mock_response = MagicMock()
        mock_response.model_name = "test-model"
        mock_response.parts = [MagicMock(side_effect=Exception("Error"))]

        # Should catch the error and not crash
        update_ai_client_span(span, mock_response)

        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    # Output extraction failed gracefully: no output data on the span
    assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in chat_spans[0]["attributes"]
    assert transaction is not None


@pytest.mark.asyncio
async def test_message_with_system_prompt_part(sentry_init, capture_items):
    """
    Test that SystemPromptPart is handled with correct role.
    """
    from pydantic_ai import messages

    import sentry_sdk

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create message with only a SystemPromptPart
        system_part = messages.SystemPromptPart(content="You are a helpful assistant")
        msgs = [_ModelMessage([system_part])]

        # Should handle system prompt
        span = ai_client_span(msgs, None, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    # System prompts are not user-facing input messages
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0]["attributes"]
    assert transaction is not None


@pytest.mark.asyncio
async def test_message_with_instructions(sentry_init, capture_items):
    """
    Test that messages with instructions field are handled correctly.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create message with instructions
        part = UserPromptPart(content="User message")
        msgs = [_ModelMessage([part], instructions="System instructions here")]

        # Should extract system prompt from instructions
        span = ai_client_span(msgs, None, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    data = chat_spans[0]["attributes"]
    assert "System instructions here" in data[SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
    assert transaction is not None


@pytest.mark.asyncio
async def test_chat_span_without_prompts(sentry_init, capture_items):
    """
    Test that the chat span omits messages when include_prompts is disabled.
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        part = UserPromptPart(content="test")
        span = ai_client_span([_ModelMessage([part])], None, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    # Messages should not be captured with include_prompts disabled
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0]["attributes"]
    assert transaction is not None


@pytest.mark.asyncio
async def test_model_name_exception_in_callable(sentry_init, capture_items):
    """
    Test model-name extraction handles exceptions in a name() callable.
    """
    from unittest.mock import MagicMock

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Create model with callable name that raises exception
    mock_model = MagicMock()
    del mock_model.model_name
    mock_model.name = MagicMock(side_effect=Exception("Error"))

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([], None, mock_model, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    # Should fall back to str() and still report a model name
    assert chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == str(mock_model)


@pytest.mark.asyncio
async def test_model_name_from_string_model(sentry_init, capture_items):
    """
    Test that a string model is reported as the request model on the chat span.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([], None, "gpt-4", None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    assert chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gpt-4"


@pytest.mark.asyncio
async def test_model_name_from_none(sentry_init, capture_items):
    """
    Test that a None model yields a chat span without a request model.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([], None, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    assert SPANDATA.GEN_AI_REQUEST_MODEL not in chat_spans[0]["attributes"]


@pytest.mark.asyncio
async def test_chat_span_data_without_pii(sentry_init, capture_items):
    """
    Test that prompts and outputs are omitted from the chat span when PII is
    disabled, even with include_prompts=True.
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=False,  # PII disabled
    )

    part = UserPromptPart(content="secret prompt")
    response = ModelResponse(parts=[TextPart(content="secret answer")])

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([_ModelMessage([part])], None, None, None)
        update_ai_client_span(span, response)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    data = chat_spans[0]["attributes"]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in data
    assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in data


@pytest.mark.asyncio
async def test_chat_span_agent_without_toolset(sentry_init, capture_items):
    """
    Test that chat span creation handles an agent without a toolset.
    """
    from unittest.mock import MagicMock

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    mock_agent = MagicMock()
    mock_agent.model = None
    mock_agent.model_settings = None
    del mock_agent._function_toolset

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Should not raise
        span = ai_client_span([], mock_agent, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS not in chat_spans[0]["attributes"]
    assert transaction is not None


@pytest.mark.asyncio
async def test_available_tools_schema_on_chat_span(sentry_init, capture_items):
    """
    Test that tool schemas are extracted onto the chat span.
    """
    import json as jsonlib
    from unittest.mock import MagicMock

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Create agent with toolset containing schema
    mock_agent = MagicMock()
    mock_agent.model = None
    mock_agent.model_settings = None
    mock_tool = MagicMock()
    mock_schema = MagicMock()
    mock_schema.description = "Test tool description"
    mock_schema.json_schema = {"type": "object", "properties": {}}
    mock_tool.function_schema = mock_schema

    mock_agent._function_toolset.tools = {"test_tool": mock_tool}

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test"):
        span = ai_client_span([], mock_agent, None, None)
        span.finish()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    tools = jsonlib.loads(
        chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
    )
    assert tools == [
        {
            "name": "test_tool",
            "description": "Test tool description",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


@pytest.mark.asyncio
async def test_execute_tool_span_creation(sentry_init, capture_items):
    """
    Test direct creation of execute_tool span.
    """
    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.execute_tool import (
        execute_tool_span,
        update_execute_tool_span,
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create execute_tool span
        with execute_tool_span("test_tool", {"arg": "value"}, None, "function") as span:
            # Update with result
            update_execute_tool_span(span, {"result": "success"})

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_execute_tool_span_with_mcp_type(sentry_init, capture_items):
    """
    Test execute_tool span with MCP tool type.
    """
    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.execute_tool import (
        execute_tool_span,
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create execute_tool span with mcp type
        with execute_tool_span("mcp_tool", {"arg": "value"}, None, "mcp") as span:
            # Verify type is set
            assert span is not None

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_execute_tool_span_without_prompts(sentry_init, capture_items):
    """
    Test that the execute_tool span respects the include_prompts setting.
    """
    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.execute_tool import (
        execute_tool_span,
        update_execute_tool_span,
    )

    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create execute_tool span
        with execute_tool_span("test_tool", {"arg": "value"}, None, "function") as span:
            # Update with result - should not set input/output
            update_execute_tool_span(span, {"result": "success"})

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_execute_tool_span_with_none_args(sentry_init, capture_items):
    """
    Test execute_tool span with None args.
    """
    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.execute_tool import execute_tool_span

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create execute_tool span with None args
        with execute_tool_span("test_tool", None, None, "function") as span:
            assert span is not None

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_update_execute_tool_span_with_none_span(sentry_init, capture_items):
    """
    Test that update_execute_tool_span handles None span gracefully.
    """
    from sentry_sdk.integrations.pydantic_ai.spans.execute_tool import (
        update_execute_tool_span,
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Update with None span - should not raise
    update_execute_tool_span(None, {"result": "success"})

    # Should not crash
    assert True


@pytest.mark.asyncio
async def test_update_execute_tool_span_with_none_result(sentry_init, capture_items):
    """
    Test that update_execute_tool_span handles None result gracefully.
    """
    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.execute_tool import (
        execute_tool_span,
        update_execute_tool_span,
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create execute_tool span
        with execute_tool_span("test_tool", {"arg": "value"}, None, "function") as span:
            # Update with None result
            update_execute_tool_span(span, None)

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_tool_execution_without_span_context(sentry_init, capture_items):
    """
    Test that tool execution patch handles case when no span context exists.
    This tests the code path where current_span is None in _patch_tool_execution.
    """
    # Import the patching function

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    # Create a simple agent with no tools (won't have function_toolset)
    agent = Agent("test", name="test_no_span")

    # Call without span context (no transaction active)
    # The patches should handle this gracefully
    try:
        # This will fail because we're not in a transaction, but it should not crash
        await agent.run("test")
    except Exception:
        # Expected to fail, that's okay
        pass

    # Should not crash
    assert True


@pytest.mark.asyncio
async def test_invoke_agent_span_with_callable_instruction(sentry_init, capture_items):
    """
    Test that invoke_agent_span skips callable instructions correctly.
    """
    from unittest.mock import MagicMock

    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.invoke_agent import invoke_agent_span

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create mock agent with callable instruction
        mock_agent = MagicMock()
        mock_agent.name = "test_agent"
        mock_agent._system_prompts = []

        # Add both string and callable instructions
        mock_callable = lambda: "Dynamic instruction"
        mock_agent._instructions = ["Static instruction", mock_callable]

        # Create span
        span = invoke_agent_span("Test prompt", mock_agent, None, None)
        span.finish()

    # Should not crash (callable should be skipped)
    assert transaction is not None


@pytest.mark.asyncio
async def test_invoke_agent_span_with_string_instructions(sentry_init, capture_items):
    """
    Test that invoke_agent_span handles string instructions (not list).
    """
    from unittest.mock import MagicMock

    import sentry_sdk
    from sentry_sdk.integrations.pydantic_ai.spans.invoke_agent import invoke_agent_span

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        # Create mock agent with string instruction
        mock_agent = MagicMock()
        mock_agent.name = "test_agent"
        mock_agent._system_prompts = []
        mock_agent._instructions = "Single instruction string"

        # Create span
        span = invoke_agent_span("Test prompt", mock_agent, None, None)
        span.finish()

    # Should not crash
    assert transaction is not None


@pytest.mark.asyncio
async def test_ai_client_span_with_streaming_flag(sentry_init, capture_items):
    """
    Test that ai_client_span reads the streaming flag from the agent context.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        push_agent(None, is_streaming=True)
        try:
            span = ai_client_span([], None, None, None)
            span.finish()
        finally:
            pop_agent()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    assert chat_spans[0]["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert transaction is not None


@pytest.mark.asyncio
async def test_ai_client_span_gets_agent_from_context(sentry_init, capture_items):
    """
    Test that ai_client_span gets the agent from the agent context when not
    passed explicitly.
    """
    from unittest.mock import MagicMock

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    mock_agent = MagicMock()
    mock_agent.name = "test_agent"
    mock_agent._function_toolset = None

    items = capture_items()
    with sentry_sdk.start_transaction(op="test", name="test") as transaction:
        push_agent(mock_agent)
        try:
            # Create ai_client span without passing agent
            span = ai_client_span([], None, None, None)
            span.finish()
        finally:
            pop_agent()

    sentry_sdk.flush()
    chat_spans = _chat_spans(items)
    assert len(chat_spans) == 1
    assert chat_spans[0]["attributes"][SPANDATA.GEN_AI_AGENT_NAME] == "test_agent"
    assert transaction is not None


class _ModelMessage:
    """Minimal stand-in for a pydantic-ai model message (parts + instructions)."""

    def __init__(self, parts, instructions=None):
        self.parts = parts
        self.instructions = instructions


def _chat_spans(items):
    """Chat span payloads from captured telemetry items.

    GenAI chat spans are sent as standalone ``span`` envelope items; their
    attribute values are already unwrapped by the ``capture_items`` fixture.
    """
    return [
        item.payload
        for item in items
        if item.type == "span"
        and item.payload["attributes"].get("sentry.op") == "gen_ai.chat"
    ]


def _get_messages_from_span(span_data):
    """Helper to extract and parse messages from span data."""
    messages_data = span_data["gen_ai.request.messages"]
    return (
        json.loads(messages_data) if isinstance(messages_data, str) else messages_data
    )


def _find_binary_content(messages_data, expected_modality, expected_mime_type):
    """Helper to find and verify binary content in messages."""
    for msg in messages_data:
        if "content" not in msg:
            continue
        for content_item in msg["content"]:
            if content_item.get("type") == "blob":
                assert content_item["modality"] == expected_modality
                assert content_item["mime_type"] == expected_mime_type
                assert content_item["content"] == BLOB_DATA_SUBSTITUTE
                return True
    return False


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_binary_content_encoding_image(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """Test that BinaryContent with image data is properly encoded in messages."""
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    binary_content = BinaryContent(
        data=b"fake_image_data_12345", media_type="image/png"
    )
    user_part = UserPromptPart(content=["Look at this image:", binary_content])
    messages = [_ModelMessage([user_part])]

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(op="test", name="test"):
            span = ai_client_span(messages, None, None, None)
            span.finish()

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1
        messages_data = _get_messages_from_span(chat_spans[0]["attributes"])
        assert _find_binary_content(messages_data, "image", "image/png")
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(op="test", name="test"):
            span = ai_client_span(messages, None, None, None)
            span.finish()

        (event,) = events
        chat_spans = [s for s in event["spans"] if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1
        messages_data = _get_messages_from_span(chat_spans[0]["data"])
        assert _find_binary_content(messages_data, "image", "image/png")


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_binary_content_encoding_mixed_content(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """Test that BinaryContent mixed with text content is properly handled."""
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    binary_content = BinaryContent(data=b"fake_image_bytes", media_type="image/jpeg")
    user_part = UserPromptPart(
        content=["Here is an image:", binary_content, "What do you see?"]
    )
    messages = [_ModelMessage([user_part])]

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(op="test", name="test"):
            span = ai_client_span(messages, None, None, None)
            span.finish()

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1
        messages_data = _get_messages_from_span(chat_spans[0]["attributes"])

        # Verify both text and binary content are present
        found_text = any(
            content_item.get("type") == "text"
            for msg in messages_data
            if "content" in msg
            for content_item in msg["content"]
        )
        assert found_text, "Text content should be found"
        assert _find_binary_content(messages_data, "image", "image/jpeg")
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(op="test", name="test"):
            span = ai_client_span(messages, None, None, None)
            span.finish()

        (event,) = events
        chat_spans = [s for s in event["spans"] if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1
        messages_data = _get_messages_from_span(chat_spans[0]["data"])

        # Verify both text and binary content are present
        found_text = any(
            content_item.get("type") == "text"
            for msg in messages_data
            if "content" in msg
            for content_item in msg["content"]
        )
        assert found_text, "Text content should be found"
        assert _find_binary_content(messages_data, "image", "image/jpeg")


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_binary_content_in_agent_run(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """Test that BinaryContent in actual agent run is properly captured in spans."""
    agent = Agent("test", name="test_binary_agent")

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    binary_content = BinaryContent(
        data=b"fake_image_data_for_testing", media_type="image/png"
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await agent.run(["Analyze this image:", binary_content])

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]
        if "gen_ai.request.messages" in chat_span["attributes"]:
            messages_str = str(chat_span["attributes"]["gen_ai.request.messages"])

            assert any(
                keyword in messages_str for keyword in ["blob", "image", "base64"]
            )
    else:
        events = capture_events()

        await agent.run(["Analyze this image:", binary_content])

        (transaction,) = events
        chat_spans = [s for s in transaction["spans"] if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1

        chat_span = chat_spans[0]
        if "gen_ai.request.messages" in chat_span["data"]:
            messages_str = str(chat_span["data"]["gen_ai.request.messages"])
            assert any(
                keyword in messages_str for keyword in ["blob", "image", "base64"]
            )


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_usage_data_with_cache_tokens(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """Test that cache_read_tokens and cache_write_tokens are tracked."""
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    usage = RequestUsage(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=80,
        cache_write_tokens=20,
    )
    response = ModelResponse(parts=[TextPart(content="ok")], usage=usage)

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(op="test", name="test"):
            span = ai_client_span([], None, None, None)
            update_ai_client_span(span, response)
            span.finish()

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1
        attributes = chat_spans[0]["attributes"]
        assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
        assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(op="test", name="test"):
            span = ai_client_span([], None, None, None)
            update_ai_client_span(span, response)
            span.finish()

        (event,) = events
        chat_spans = [s for s in event["spans"] if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1
        span_data = chat_spans[0]["data"]
        assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
        assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "url,image_url_kwargs,expected_content",
    [
        pytest.param(
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            {},
            BLOB_DATA_SUBSTITUTE,
            id="base64_data_url",
        ),
        pytest.param(
            "https://example.com/image.png",
            {},
            "https://example.com/image.png",
            id="http_url_no_redaction",
        ),
        pytest.param(
            "https://example.com/api?data=iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            {"media_type": "image/png"},
            "https://example.com/api?data=iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            id="http_url_with_base64_query_param",
        ),
        pytest.param(
            "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4=",
            {},
            BLOB_DATA_SUBSTITUTE,
            id="complex_mime_type",
        ),
        pytest.param(
            "data:image/png;name=file.png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            {},
            BLOB_DATA_SUBSTITUTE,
            id="optional_parameters",
        ),
        pytest.param(
            "data:text/plain;charset=utf-8;name=hello.txt;base64,SGVsbG8sIFdvcmxkIQ==",
            {},
            BLOB_DATA_SUBSTITUTE,
            id="multiple_optional_parameters",
        ),
    ],
)
def test_image_url_base64_content_in_span(
    sentry_init,
    capture_events,
    capture_items,
    url,
    image_url_kwargs,
    expected_content,
    stream_gen_ai_spans,
    span_streaming,
):
    from sentry_sdk.integrations.pydantic_ai.spans.ai_client import ai_client_span

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    found_image = False
    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(op="test", name="test"):
            image_url = ImageUrl(url=url, **image_url_kwargs)
            user_part = UserPromptPart(content=["Look at this image:", image_url])
            mock_msg = MagicMock()
            mock_msg.parts = [user_part]
            mock_msg.instructions = None

            span = ai_client_span([mock_msg], None, None, None)
            span.finish()

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        assert len(chat_spans) == 1
        messages_data = _get_messages_from_span(chat_spans[0]["attributes"])

        for msg in messages_data:
            if "content" not in msg:
                continue
            for content_item in msg["content"]:
                if content_item.get("type") == "image":
                    found_image = True
                    assert content_item["content"] == expected_content
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(op="test", name="test"):
            image_url = ImageUrl(url=url, **image_url_kwargs)
            user_part = UserPromptPart(content=["Look at this image:", image_url])
            mock_msg = MagicMock()
            mock_msg.parts = [user_part]
            mock_msg.instructions = None

            span = ai_client_span([mock_msg], None, None, None)
            span.finish()

        (event,) = events
        chat_spans = [s for s in event["spans"] if s["op"] == "gen_ai.chat"]
        assert len(chat_spans) == 1
        messages_data = _get_messages_from_span(chat_spans[0]["data"])

        for msg in messages_data:
            if "content" not in msg:
                continue
            for content_item in msg["content"]:
                if content_item.get("type") == "image":
                    found_image = True
                    assert content_item["content"] == expected_content

    assert found_image, "Image content item should be found in messages data"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, image_url_kwargs, expected_content",
    [
        pytest.param(
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            {},
            BLOB_DATA_SUBSTITUTE,
            id="base64_data_url_redacted",
        ),
        pytest.param(
            "https://example.com/image.png",
            {},
            "https://example.com/image.png",
            id="http_url_no_redaction",
        ),
        pytest.param(
            "https://example.com/api?data=iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            {},
            "https://example.com/api?data=iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            id="http_url_with_base64_query_param",
        ),
        pytest.param(
            "https://example.com/api?data=iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            {"media_type": "image/png"},
            "https://example.com/api?data=iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs",
            id="http_url_with_base64_query_param_and_media_type",
        ),
    ],
)
async def test_invoke_agent_image_url(
    sentry_init,
    capture_events,
    capture_items,
    url,
    image_url_kwargs,
    expected_content,
    stream_gen_ai_spans,
    span_streaming,
):
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    agent = Agent("test", name="test_image_url_agent")

    image_url = ImageUrl(url=url, **image_url_kwargs)

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        await agent.run([image_url, "Describe this image"])

        found_image = False

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        chat_spans = [
            s for s in spans if s["attributes"].get("sentry.op", "") == "gen_ai.chat"
        ]
        messages_data = _get_messages_from_span(chat_spans[0]["attributes"])
        for msg in messages_data:
            if "content" not in msg:
                continue
            for content_item in msg["content"]:
                if content_item.get("type") == "image":
                    assert content_item["content"] == expected_content
                    found_image = True
    else:
        events = capture_events()

        await agent.run([image_url, "Describe this image"])

        (transaction,) = events

        found_image = False

        chat_spans = [s for s in transaction["spans"] if s["op"] == "gen_ai.chat"]
        messages_data = _get_messages_from_span(chat_spans[0]["data"])
        for msg in messages_data:
            if "content" not in msg:
                continue
            for content_item in msg["content"]:
                if content_item.get("type") == "image":
                    assert content_item["content"] == expected_content
                    found_image = True

    assert found_image, "Image content item should be found in messages data"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_tool_description_in_execute_tool_span(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    Test that tool description from the tool's docstring is included in execute_tool spans.
    """
    agent = Agent(
        "test",
        name="test_agent",
        system_prompt="You are a helpful test assistant.",
    )

    @agent.tool_plain
    def multiply_numbers(a: int, b: int) -> int:
        """Multiply two numbers and return the product."""
        return a * b

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        result = await agent.run("What is 5 times 3?")
        assert result is not None

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        tool_spans = [
            s
            for s in spans
            if s["attributes"].get("sentry.op", "") == "gen_ai.execute_tool"
        ]

        assert len(tool_spans) >= 1

        tool_span = tool_spans[0]

        assert tool_span["attributes"]["gen_ai.tool.name"] == "multiply_numbers"
        assert SPANDATA.GEN_AI_TOOL_DESCRIPTION in tool_span["attributes"]
        assert (
            "Multiply two numbers"
            in tool_span["attributes"][SPANDATA.GEN_AI_TOOL_DESCRIPTION]
        )
    else:
        events = capture_events()

        result = await agent.run("What is 5 times 3?")
        assert result is not None

        (transaction,) = events
        spans = transaction["spans"]

        tool_spans = [s for s in spans if s["op"] == "gen_ai.execute_tool"]

        assert len(tool_spans) >= 1

        tool_span = tool_spans[0]

        assert tool_span["data"]["gen_ai.tool.name"] == "multiply_numbers"
        assert SPANDATA.GEN_AI_TOOL_DESCRIPTION in tool_span["data"]
        assert (
            "Multiply two numbers"
            in tool_span["data"][SPANDATA.GEN_AI_TOOL_DESCRIPTION]
        )


def _spans_by_op(items, events, streaming):
    """Normalize captured spans to a list of (op, data) tuples.

    Works for both the span-streaming/gen-AI-span-streaming payloads and the
    classic transaction payload so data collection assertions can be shared.
    """
    if streaming:
        sentry_sdk.flush()
        return [
            (
                item.payload["attributes"].get("sentry.op", ""),
                item.payload["attributes"],
            )
            for item in items
            if item.type == "span"
        ]

    (transaction,) = events
    return [(span["op"], span["data"]) for span in transaction["spans"]]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,expect_inputs,expect_available_tools",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            False,
            False,
            True,
            True,
            id="gen-ai-inputs-enabled-overrides-pii-and-include-prompts-disabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            True,
            True,
            False,
            False,
            id="gen-ai-inputs-disabled-overrides-pii-enabled",
        ),
        pytest.param(
            {},
            False,
            False,
            True,
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            False,
            False,
            True,
            True,
            id="gen-ai-outputs-disabled-does-not-affect-inputs",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            True,
            id="no-data-collection-pii-and-include-prompts-enabled-collects",
        ),
        pytest.param(
            None,
            True,
            False,
            False,
            True,
            id="no-data-collection-include-prompts-disabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            True,
            id="no-data-collection-pii-disabled",
        ),
    ],
)
@pytest.mark.asyncio
async def test_data_collection_gen_ai_inputs_gates_request_messages_tool_inputs_and_available_tools(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    data_collection,
    send_default_pii,
    include_prompts,
    expect_inputs,
    expect_available_tools,
    stream_gen_ai_spans,
    span_streaming,
):
    init_kwargs = {
        "integrations": [PydanticAIIntegration(include_prompts=include_prompts)],
        "traces_sample_rate": 1.0,
        "send_default_pii": send_default_pii,
        "stream_gen_ai_spans": stream_gen_ai_spans,
        "trace_lifecycle": "stream" if span_streaming else "static",
    }
    if data_collection is not None:
        init_kwargs["data_collection"] = data_collection

    sentry_init(**init_kwargs)

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def add_numbers(a: int, b: int) -> int:
        return a + b

    streaming = span_streaming or stream_gen_ai_spans
    if streaming:
        items = capture_items("span")
        events = None
    else:
        items = None
        events = capture_events()

    result = await test_agent.run("What is 5 + 3?")
    assert result is not None

    spans = _spans_by_op(items, events, streaming)

    chat_spans = [data for op, data in spans if op == "gen_ai.chat"]
    tool_spans = [data for op, data in spans if op == "gen_ai.execute_tool"]

    assert len(chat_spans) >= 1
    assert len(tool_spans) >= 1

    for chat_span in chat_spans:
        if expect_inputs:
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES in chat_span
            assert (
                "helpful test assistant"
                in chat_span[SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            )
        else:
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_span
            assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_span

        # Non-PII data is unaffected by the gate
        assert SPANDATA.GEN_AI_REQUEST_MODEL in chat_span

    # Both the chat and the invoke_agent spans list the agent's available tools
    for op, span_data in spans:
        if op not in ("gen_ai.chat", "gen_ai.invoke_agent"):
            continue

        if expect_available_tools:
            assert "add_numbers" in span_data[SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
        else:
            assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS not in span_data

    for tool_span in tool_spans:
        if expect_inputs:
            assert SPANDATA.GEN_AI_TOOL_INPUT in tool_span
        else:
            assert SPANDATA.GEN_AI_TOOL_INPUT not in tool_span

        # Non-PII data is unaffected by the gate
        assert tool_span[SPANDATA.GEN_AI_TOOL_NAME] == "add_numbers"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,expect_outputs",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-and-include-prompts-disabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-enabled",
        ),
        pytest.param(
            {},
            False,
            False,
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            False,
            False,
            True,
            id="gen-ai-inputs-disabled-does-not-affect-outputs",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            id="no-data-collection-pii-and-include-prompts-enabled-collects",
        ),
        pytest.param(
            None,
            True,
            False,
            False,
            id="no-data-collection-include-prompts-disabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            id="no-data-collection-pii-disabled",
        ),
    ],
)
@pytest.mark.asyncio
async def test_data_collection_gen_ai_outputs_gates_response_text_and_tool_outputs(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    data_collection,
    send_default_pii,
    include_prompts,
    expect_outputs,
    stream_gen_ai_spans,
    span_streaming,
):
    init_kwargs = {
        "integrations": [PydanticAIIntegration(include_prompts=include_prompts)],
        "traces_sample_rate": 1.0,
        "send_default_pii": send_default_pii,
        "stream_gen_ai_spans": stream_gen_ai_spans,
        "trace_lifecycle": "stream" if span_streaming else "static",
    }
    if data_collection is not None:
        init_kwargs["data_collection"] = data_collection

    sentry_init(**init_kwargs)

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def add_numbers(a: int, b: int) -> int:
        return a + b

    streaming = span_streaming or stream_gen_ai_spans
    if streaming:
        items = capture_items("transaction", "span")
        events = None
    else:
        items = None
        events = capture_events()

    result = await test_agent.run("What is 5 + 3?")
    assert result is not None

    spans = _spans_by_op(items, events, streaming)

    # The invoke_agent span is either a child span or, when it is the segment
    # span, the transaction itself.
    invoke_agent_data = next(
        (data for op, data in spans if op == "gen_ai.invoke_agent"), None
    )
    if invoke_agent_data is None:
        if streaming:
            (transaction,) = (
                item.payload for item in items if item.type == "transaction"
            )
        else:
            (transaction,) = events
        invoke_agent_data = transaction["contexts"]["trace"]["data"]

    chat_spans = [data for op, data in spans if op == "gen_ai.chat"]
    tool_spans = [data for op, data in spans if op == "gen_ai.execute_tool"]

    assert len(chat_spans) >= 1
    assert len(tool_spans) >= 1

    if expect_outputs:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in invoke_agent_data
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in invoke_agent_data

    # Every part of the output message follows the outputs gate, so the
    # attribute is dropped entirely when outputs are disabled.
    response_part_types = set()
    for chat_span in chat_spans:
        for message in json.loads(chat_span.get(SPANDATA.GEN_AI_OUTPUT_MESSAGES, "[]")):
            for part in message["parts"]:
                response_part_types.add(part["type"])

    if expect_outputs:
        assert "text" in response_part_types
    else:
        assert response_part_types == set()
        for chat_span in chat_spans:
            assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in chat_span

    for tool_span in tool_spans:
        if expect_outputs:
            assert SPANDATA.GEN_AI_TOOL_OUTPUT in tool_span
        else:
            assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_span

        # Non-PII data is unaffected by the gate
        assert tool_span[SPANDATA.GEN_AI_TOOL_NAME] == "add_numbers"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "gen_ai,expect_outputs",
    [
        pytest.param(
            {"inputs": True, "outputs": False},
            False,
            id="gen-ai-outputs-disabled-drops-text-and-tool-calls",
        ),
        pytest.param(
            {"inputs": False, "outputs": True},
            True,
            id="gen-ai-inputs-disabled-does-not-affect-output-messages",
        ),
        pytest.param(
            {"inputs": True, "outputs": True},
            True,
            id="gen-ai-inputs-and-outputs-enabled",
        ),
        pytest.param(
            {"inputs": False, "outputs": False},
            False,
            id="gen-ai-inputs-and-outputs-disabled",
        ),
    ],
)
@pytest.mark.asyncio
async def test_data_collection_gen_ai_output_message_parts_follow_outputs_gate(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    gen_ai,
    expect_outputs,
    stream_gen_ai_spans,
    span_streaming,
):
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
        data_collection={"gen_ai": gen_ai},
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def add_numbers(a: int, b: int) -> int:
        return a + b

    streaming = span_streaming or stream_gen_ai_spans
    if streaming:
        items = capture_items("transaction", "span")
        events = None
    else:
        items = None
        events = capture_events()

    result = await test_agent.run("What is 5 + 3?")
    assert result is not None

    spans = _spans_by_op(items, events, streaming)
    chat_spans = [data for op, data in spans if op == "gen_ai.chat"]

    # The test model calls the tool on the first response and answers with text
    # on the second, so a single run produces both part types.
    assert len(chat_spans) >= 2

    parts = []
    for chat_span in chat_spans:
        for message in json.loads(chat_span.get(SPANDATA.GEN_AI_OUTPUT_MESSAGES, "[]")):
            parts.extend(message["parts"])

    part_types = {part["type"] for part in parts}

    if expect_outputs:
        assert "text" in part_types

        tool_calls = [part for part in parts if part["type"] == "tool_call"]
        assert len(tool_calls) >= 1
        assert tool_calls[0]["name"] == "add_numbers"
        assert tool_calls[0]["arguments"]
    else:
        assert part_types == set()

    for chat_span in chat_spans:
        # The response model is not PII, so it is recorded regardless of the gates
        assert SPANDATA.GEN_AI_RESPONSE_MODEL in chat_span

        if not expect_outputs:
            assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in chat_span


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_data_collection_gen_ai_request_messages_keep_tool_returns_when_outputs_disabled(
    sentry_init,
    capture_events,
    capture_items,
    get_test_agent,
    stream_gen_ai_spans,
    span_streaming,
):
    """
    A tool return value is an output on the `gen_ai.execute_tool` span, so
    `outputs: False` drops it there. The same value is then fed back into the
    next model call, where it is an input, so `inputs: True` records it under
    `gen_ai.request.messages`. This is intended: the gates describe a value's
    position in the call being instrumented, not which party produced it.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
        data_collection={"gen_ai": {"inputs": True, "outputs": False}},
    )

    test_agent = get_test_agent()

    @test_agent.tool_plain
    def add_numbers(a: int, b: int) -> int:
        return a + b

    streaming = span_streaming or stream_gen_ai_spans
    if streaming:
        items = capture_items("transaction", "span")
        events = None
    else:
        items = None
        events = capture_events()

    result = await test_agent.run("What is 5 + 3?")
    assert result is not None

    spans = _spans_by_op(items, events, streaming)
    chat_spans = [data for op, data in spans if op == "gen_ai.chat"]
    tool_spans = [data for op, data in spans if op == "gen_ai.execute_tool"]

    assert len(chat_spans) >= 2
    assert len(tool_spans) >= 1

    # Derive the expected return value from the arguments the model actually
    # sent, so the assertion does not depend on the test model's defaults.
    tool_span = tool_spans[0]
    assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_span
    tool_input = json.loads(tool_span[SPANDATA.GEN_AI_TOOL_INPUT])
    expected_tool_return = str(tool_input["a"] + tool_input["b"])

    tool_messages = [
        message
        for chat_span in chat_spans
        for message in json.loads(chat_span.get(SPANDATA.GEN_AI_REQUEST_MESSAGES, "[]"))
        if message["role"] == "tool"
    ]

    assert len(tool_messages) >= 1
    assert tool_messages[0]["tool_call_id"] == "add_numbers"
    assert tool_messages[0]["content"] == [
        {"type": "text", "text": expected_tool_return}
    ]

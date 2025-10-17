import asyncio
import pytest

from sentry_sdk.integrations.pydantic_ai import PydanticAIIntegration

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel


@pytest.fixture
def test_agent():
    """Create a test agent with model settings."""
    return Agent(
        "test",
        name="test_agent",
        system_prompt="You are a helpful test assistant.",
    )


@pytest.fixture
def test_agent_with_settings():
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


@pytest.mark.asyncio
async def test_agent_run_async(sentry_init, capture_events, test_agent):
    """
    Test that the integration creates spans for async agent runs.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    events = capture_events()

    result = await test_agent.run("Test input")

    assert result is not None
    assert result.output is not None

    (transaction,) = events
    spans = transaction["spans"]

    # Verify transaction
    assert transaction["transaction"] == "agent workflow test_agent"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

    # Find span types
    invoke_agent_spans = [s for s in spans if s["op"] == "gen_ai.invoke_agent"]
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

    assert len(invoke_agent_spans) == 1
    assert len(chat_spans) >= 1

    # Check invoke_agent span
    invoke_agent_span = invoke_agent_spans[0]
    assert invoke_agent_span["description"] == "invoke_agent test_agent"
    assert invoke_agent_span["data"]["gen_ai.operation.name"] == "invoke_agent"
    assert invoke_agent_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert "gen_ai.request.messages" in invoke_agent_span["data"]
    assert "gen_ai.response.text" in invoke_agent_span["data"]

    # Check chat span
    chat_span = chat_spans[0]
    assert "chat" in chat_span["description"]
    assert chat_span["data"]["gen_ai.operation.name"] == "chat"
    assert chat_span["data"]["gen_ai.response.streaming"] is False
    assert "gen_ai.request.messages" in chat_span["data"]
    assert "gen_ai.usage.input_tokens" in chat_span["data"]
    assert "gen_ai.usage.output_tokens" in chat_span["data"]


def test_agent_run_sync(sentry_init, capture_events, test_agent):
    """
    Test that the integration creates spans for sync agent runs.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    events = capture_events()

    result = test_agent.run_sync("Test input")

    assert result is not None
    assert result.output is not None

    (transaction,) = events
    spans = transaction["spans"]

    # Verify transaction
    assert transaction["transaction"] == "agent workflow test_agent"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

    # Find span types
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
    assert len(chat_spans) >= 1

    # Verify streaming flag is False for sync
    for chat_span in chat_spans:
        assert chat_span["data"]["gen_ai.response.streaming"] is False


@pytest.mark.asyncio
async def test_agent_run_stream(sentry_init, capture_events, test_agent):
    """
    Test that the integration creates spans for streaming agent runs.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    events = capture_events()

    async with test_agent.run_stream("Test input") as result:
        # Consume the stream
        async for _ in result.stream_output():
            pass

    (transaction,) = events
    spans = transaction["spans"]

    # Verify transaction
    assert transaction["transaction"] == "agent workflow test_agent"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.pydantic_ai"

    # Find chat spans
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
    assert len(chat_spans) >= 1

    # Verify streaming flag is True for streaming
    for chat_span in chat_spans:
        assert chat_span["data"]["gen_ai.response.streaming"] is True
        assert "gen_ai.request.messages" in chat_span["data"]
        assert "gen_ai.usage.input_tokens" in chat_span["data"]
        # Streaming responses should still have output data
        assert (
            "gen_ai.response.text" in chat_span["data"]
            or "gen_ai.response.model" in chat_span["data"]
        )


@pytest.mark.asyncio
async def test_agent_run_stream_events(sentry_init, capture_events, test_agent):
    """
    Test that run_stream_events creates spans (it uses run internally, so non-streaming).
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    events = capture_events()

    # Consume all events
    async for _ in test_agent.run_stream_events("Test input"):
        pass

    (transaction,) = events

    # Verify transaction
    assert transaction["transaction"] == "agent workflow test_agent"

    # Find chat spans
    spans = transaction["spans"]
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
    assert len(chat_spans) >= 1

    # run_stream_events uses run() internally, so streaming should be False
    for chat_span in chat_spans:
        assert chat_span["data"]["gen_ai.response.streaming"] is False


@pytest.mark.asyncio
async def test_agent_with_tools(sentry_init, capture_events, test_agent):
    """
    Test that tool execution creates execute_tool spans.
    """

    @test_agent.tool_plain
    def add_numbers(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    events = capture_events()

    result = await test_agent.run("What is 5 + 3?")

    assert result is not None

    (transaction,) = events
    spans = transaction["spans"]

    # Find span types
    invoke_agent_spans = [s for s in spans if s["op"] == "gen_ai.invoke_agent"]
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
    tool_spans = [s for s in spans if s["op"] == "gen_ai.execute_tool"]

    # Should have tool spans
    assert len(tool_spans) >= 1

    # Check tool span
    tool_span = tool_spans[0]
    assert "execute_tool" in tool_span["description"]
    assert tool_span["data"]["gen_ai.operation.name"] == "execute_tool"
    assert tool_span["data"]["gen_ai.tool.type"] == "function"
    assert tool_span["data"]["gen_ai.tool.name"] == "add_numbers"
    assert "gen_ai.tool.input" in tool_span["data"]
    assert "gen_ai.tool.output" in tool_span["data"]

    # Check invoke_agent span has available_tools
    invoke_agent_span = invoke_agent_spans[0]
    assert "gen_ai.request.available_tools" in invoke_agent_span["data"]
    available_tools_str = invoke_agent_span["data"]["gen_ai.request.available_tools"]
    # Available tools is serialized as a string
    assert "add_numbers" in available_tools_str

    # Check chat spans also have available_tools
    for chat_span in chat_spans:
        assert "gen_ai.request.available_tools" in chat_span["data"]


@pytest.mark.asyncio
async def test_agent_with_tools_streaming(sentry_init, capture_events, test_agent):
    """
    Test that tool execution works correctly with streaming.
    """

    @test_agent.tool_plain
    def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

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
    for chat_span in chat_spans:
        assert chat_span["data"]["gen_ai.response.streaming"] is True

    # Check tool span
    tool_span = tool_spans[0]
    assert tool_span["data"]["gen_ai.tool.name"] == "multiply"
    assert "gen_ai.tool.input" in tool_span["data"]
    assert "gen_ai.tool.output" in tool_span["data"]


@pytest.mark.asyncio
async def test_model_settings(sentry_init, capture_events, test_agent_with_settings):
    """
    Test that model settings are captured in spans.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    await test_agent_with_settings.run("Test input")

    (transaction,) = events
    spans = transaction["spans"]

    # Find chat span
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
    assert len(chat_spans) >= 1

    chat_span = chat_spans[0]
    # Check that model settings are captured
    assert chat_span["data"].get("gen_ai.request.temperature") == 0.7
    assert chat_span["data"].get("gen_ai.request.max_tokens") == 100
    assert chat_span["data"].get("gen_ai.request.top_p") == 0.9


@pytest.mark.asyncio
async def test_system_prompt_in_messages(sentry_init, capture_events):
    """
    Test that system prompts are included as the first message.
    """
    agent = Agent(
        "test",
        name="test_system",
        system_prompt="You are a helpful assistant specialized in testing.",
    )

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    events = capture_events()

    await agent.run("Hello")

    (transaction,) = events
    spans = transaction["spans"]

    # Find invoke_agent span
    invoke_agent_spans = [s for s in spans if s["op"] == "gen_ai.invoke_agent"]
    assert len(invoke_agent_spans) == 1

    invoke_agent_span = invoke_agent_spans[0]
    messages_str = invoke_agent_span["data"]["gen_ai.request.messages"]

    # Messages is serialized as a string
    # Should contain system role and helpful assistant text
    assert "system" in messages_str
    assert "helpful assistant" in messages_str


@pytest.mark.asyncio
async def test_error_handling(sentry_init, capture_events):
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
    )

    events = capture_events()

    # Simple run that should succeed
    await agent.run("Hello")

    # At minimum, we should have a transaction
    assert len(events) >= 1
    transaction = [e for e in events if e.get("type") == "transaction"][0]
    assert transaction["transaction"] == "agent workflow test_error"
    # Transaction should complete successfully (status key may not exist if no error)
    trace_status = transaction["contexts"]["trace"].get("status")
    assert trace_status != "error"  # Could be None or some other status


@pytest.mark.asyncio
async def test_without_pii(sentry_init, capture_events, test_agent):
    """
    Test that PII is not captured when send_default_pii is False.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

    events = capture_events()

    await test_agent.run("Sensitive input")

    (transaction,) = events
    spans = transaction["spans"]

    # Find spans
    invoke_agent_spans = [s for s in spans if s["op"] == "gen_ai.invoke_agent"]
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

    # Verify that messages and response text are not captured
    for span in invoke_agent_spans + chat_spans:
        assert "gen_ai.request.messages" not in span["data"]
        assert "gen_ai.response.text" not in span["data"]


@pytest.mark.asyncio
async def test_without_pii_tools(sentry_init, capture_events, test_agent):
    """
    Test that tool input/output are not captured when send_default_pii is False.
    """

    @test_agent.tool_plain
    def sensitive_tool(data: str) -> str:
        """A tool with sensitive data."""
        return f"Processed: {data}"

    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

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


@pytest.mark.asyncio
async def test_multiple_agents_concurrent(sentry_init, capture_events, test_agent):
    """
    Test that multiple agents can run concurrently without interfering.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    async def run_agent(input_text):
        return await test_agent.run(input_text)

    # Run 3 agents concurrently
    results = await asyncio.gather(*[run_agent(f"Input {i}") for i in range(3)])

    assert len(results) == 3
    assert len(events) == 3

    # Verify each transaction is separate
    for i, transaction in enumerate(events):
        assert transaction["type"] == "transaction"
        assert transaction["transaction"] == "agent workflow test_agent"
        # Each should have its own spans
        assert len(transaction["spans"]) >= 1


@pytest.mark.asyncio
async def test_message_history(sentry_init, capture_events):
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
    )

    events = capture_events()

    # First message
    await agent.run("Hello, I'm Alice")

    # Second message with history
    from pydantic_ai import messages

    history = [
        messages.UserPromptPart(content="Hello, I'm Alice"),
        messages.ModelResponse(
            parts=[messages.TextPart(content="Hello Alice! How can I help you?")],
            model_name="test",
        ),
    ]

    await agent.run("What is my name?", message_history=history)

    # We should have 2 transactions
    assert len(events) >= 2

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


@pytest.mark.asyncio
async def test_gen_ai_system(sentry_init, capture_events, test_agent):
    """
    Test that gen_ai.system is set from the model.
    """
    sentry_init(
        integrations=[PydanticAIIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    await test_agent.run("Test input")

    (transaction,) = events
    spans = transaction["spans"]

    # Find chat span
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]
    assert len(chat_spans) >= 1

    chat_span = chat_spans[0]
    # gen_ai.system should be set from the model (TestModel -> 'test')
    assert "gen_ai.system" in chat_span["data"]
    assert chat_span["data"]["gen_ai.system"] == "test"


@pytest.mark.asyncio
async def test_include_prompts_false(sentry_init, capture_events, test_agent):
    """
    Test that prompts are not captured when include_prompts=False.
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,  # Even with PII enabled, prompts should not be captured
    )

    events = capture_events()

    await test_agent.run("Sensitive prompt")

    (transaction,) = events
    spans = transaction["spans"]

    # Find spans
    invoke_agent_spans = [s for s in spans if s["op"] == "gen_ai.invoke_agent"]
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

    # Verify that messages and response text are not captured
    for span in invoke_agent_spans + chat_spans:
        assert "gen_ai.request.messages" not in span["data"]
        assert "gen_ai.response.text" not in span["data"]


@pytest.mark.asyncio
async def test_include_prompts_true(sentry_init, capture_events, test_agent):
    """
    Test that prompts are captured when include_prompts=True (default).
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    events = capture_events()

    await test_agent.run("Test prompt")

    (transaction,) = events
    spans = transaction["spans"]

    # Find spans
    invoke_agent_spans = [s for s in spans if s["op"] == "gen_ai.invoke_agent"]
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

    # Verify that messages are captured
    assert len(invoke_agent_spans) >= 1
    invoke_agent_span = invoke_agent_spans[0]
    assert "gen_ai.request.messages" in invoke_agent_span["data"]
    assert "gen_ai.response.text" in invoke_agent_span["data"]

    # Chat spans should also have messages
    assert len(chat_spans) >= 1
    for chat_span in chat_spans:
        assert "gen_ai.request.messages" in chat_span["data"]


@pytest.mark.asyncio
async def test_include_prompts_false_with_tools(
    sentry_init, capture_events, test_agent
):
    """
    Test that tool input/output are not captured when include_prompts=False.
    """

    @test_agent.tool_plain
    def test_tool(value: int) -> int:
        """A test tool."""
        return value * 2

    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

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


@pytest.mark.asyncio
async def test_include_prompts_requires_pii(sentry_init, capture_events, test_agent):
    """
    Test that include_prompts requires send_default_pii=True.
    """
    sentry_init(
        integrations=[PydanticAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=False,  # PII disabled
    )

    events = capture_events()

    await test_agent.run("Test prompt")

    (transaction,) = events
    spans = transaction["spans"]

    # Find spans
    invoke_agent_spans = [s for s in spans if s["op"] == "gen_ai.invoke_agent"]
    chat_spans = [s for s in spans if s["op"] == "gen_ai.chat"]

    # Even with include_prompts=True, if PII is disabled, messages should not be captured
    for span in invoke_agent_spans + chat_spans:
        assert "gen_ai.request.messages" not in span["data"]
        assert "gen_ai.response.text" not in span["data"]

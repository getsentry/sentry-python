import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sentry_sdk
from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration

import agents
from agents import (
    Agent,
    RunResult,
    ModelResponse,
    Usage,
    MessageOutputItem,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.items import (
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseFunctionToolCall,
    FunctionCallOutput,
)
from agents.exceptions import AgentsException


@pytest.fixture
def mock_usage():
    return Usage(
        requests=1,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        input_tokens_details=MagicMock(cached_tokens=0),
        output_tokens_details=MagicMock(reasoning_tokens=5),
    )


@pytest.fixture
def mock_model_response(mock_usage):
    return ModelResponse(
        output=[
            ResponseOutputMessage(
                id="msg_123",
                type="message",
                status="completed",
                content=[
                    ResponseOutputText(
                        text="Hello, how can I help you?",
                        type="output_text",
                        annotations=[],
                    )
                ],
                role="assistant",
            )
        ],
        usage=mock_usage,
        response_id="resp_123",
    )


@pytest.fixture
def mock_tool_call():
    return ResponseFunctionToolCall(
        id="call_123",
        call_id="call_123",
        name="test_tool",
        type="function_call",
        arguments='{"arg1": "value1"}',
        function=MagicMock(name="test_tool", arguments='{"arg1": "value1"}'),
    )


@pytest.fixture
def mock_tool_output():
    return FunctionCallOutput(tool_call_id="call_123", output="Tool execution result")


@pytest.fixture
def mock_run_result(mock_model_response, mock_tool_call, mock_tool_output):
    return RunResult(
        input="Test input",
        new_items=[
            MessageOutputItem(
                agent=MagicMock(),
                raw_item=mock_model_response.output[0],
                type="message_output_item",
            ),
            ToolCallItem(
                agent=MagicMock(), raw_item=mock_tool_call, type="tool_call_item"
            ),
            ToolCallOutputItem(
                agent=MagicMock(),
                raw_item=mock_tool_output,
                output="Tool execution result",
                type="tool_call_output_item",
            ),
        ],
        raw_responses=[mock_model_response],
        final_output="Final result",
        input_guardrail_results=[],
        output_guardrail_results=[],
        context_wrapper=MagicMock(),
        _last_agent=MagicMock(),
    )


@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=Agent)
    agent.name = "test_agent"
    agent.model = "gpt-4"
    agent.model_settings = MagicMock(
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        presence_penalty=0.0,
        frequency_penalty=0.0,
    )
    agent.tools = []
    return agent


def test_integration_initialization():
    integration = OpenAIAgentsIntegration()
    assert integration is not None


@pytest.mark.asyncio
async def test_agent_invocation_span(
    sentry_init, capture_events, mock_agent, mock_run_result
):
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        with patch("agents.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_run_result

            await agents.Runner.run(mock_agent, "Test input")

    (transaction,) = events
    spans = transaction["spans"]

    assert len(spans) > 0
    agent_span = next(span for span in spans if span["op"] == "agent.run")
    assert agent_span is not None
    assert agent_span["data"]["gen_ai.system"] == "openai"
    assert agent_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert agent_span["data"]["gen_ai.request.model"] == "gpt-4"
    assert agent_span["data"]["gen_ai.request.max_tokens"] == 100
    assert agent_span["data"]["gen_ai.request.temperature"] == 0.7


@pytest.mark.asyncio
async def test_tool_execution_span(
    sentry_init, capture_events, mock_agent, mock_run_result
):
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        with patch("agents.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_run_result

            await agents.Runner.run(mock_agent, "Test input")

    (transaction,) = events
    spans = transaction["spans"]

    tool_spans = [span for span in spans if span["op"] == "agent.tool"]
    assert len(tool_spans) > 0
    tool_span = tool_spans[0]  # type: ignore
    assert tool_span["data"]["gen_ai.tool.name"] == "test_tool"
    assert tool_span["data"]["gen_ai.tool.input"] == '{"arg1": "value1"}'
    assert tool_span["data"]["gen_ai.tool.output"] == "Tool execution result"


@pytest.mark.asyncio
async def test_llm_request_span(
    sentry_init, capture_events, mock_agent, mock_run_result
):
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        with patch("agents.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_run_result

            await agents.Runner.run(mock_agent, "Test input")

    (transaction,) = events
    spans = transaction["spans"]

    llm_spans = [span for span in spans if span["op"] == "llm.request"]
    assert len(llm_spans) > 0
    llm_span = llm_spans[0]  # type: ignore
    assert llm_span["data"]["gen_ai.request.model"] == "gpt-4"
    assert llm_span["data"]["gen_ai.usage.input_tokens"] == 10
    assert llm_span["data"]["gen_ai.usage.output_tokens"] == 20
    assert llm_span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
async def test_error_handling(sentry_init, capture_events, mock_agent):
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction") as transaction:
        with patch("agents.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = AgentsException("Test error")

            with pytest.raises(AgentsException):
                await agents.Runner.run(mock_agent, "Test input")

            # Ensure the transaction is finished and error is captured
            transaction.finish()

    (transaction,) = events
    spans = transaction["spans"]

    assert len(spans) > 0
    error_span = next(span for span in spans if span["status"] == "internal_error")
    assert error_span is not None
    assert error_span["data"]["error.type"] == "AgentsException"
    assert error_span["data"]["error.value"] == "Test error"

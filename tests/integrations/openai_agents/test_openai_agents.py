import pytest
from unittest.mock import MagicMock, patch
import os

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
    ModelSettings,
)
from agents.items import (
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseFunctionToolCall,
    FunctionCallOutput,
)


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
    agent.handoffs = []
    agent.output_type = str
    return agent


@pytest.fixture
def test_agent():
    """Create a real Agent instance for testing."""
    return Agent(
        name="test_agent",
        instructions="You are a helpful test assistant.",
        model="gpt-4",
        model_settings=ModelSettings(
            max_tokens=100,
            temperature=0.7,
            top_p=1.0,
            presence_penalty=0.0,
            frequency_penalty=0.0,
        ),
    )


def test_integration_initialization():
    integration = OpenAIAgentsIntegration()
    assert integration is not None


@pytest.mark.asyncio
async def test_agent_invocation_span(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """Test that the integration creates spans for agent invocations."""

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            mock_get_response.return_value = mock_model_response

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            with sentry_sdk.start_transaction(name="test_transaction"):
                result = await agents.Runner.run(test_agent, "Test input")

                assert result is not None
                assert result.final_output == "Hello, how can I help you?"

    (transaction,) = events
    spans = transaction["spans"]
    agent_workflow_span, invoke_agent_span, ai_client_span = spans

    assert agent_workflow_span["description"] == "test_agent workflow"

    assert invoke_agent_span["description"] == "invoke_agent test_agent"
    assert invoke_agent_span["data"]["gen_ai.operation.name"] == "invoke_agent"
    assert invoke_agent_span["data"]["gen_ai.system"] == "openai"
    assert invoke_agent_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert invoke_agent_span["data"]["gen_ai.request.max_tokens"] == 100
    assert invoke_agent_span["data"]["gen_ai.request.model"] == "gpt-4"
    assert invoke_agent_span["data"]["gen_ai.request.temperature"] == 0.7
    assert invoke_agent_span["data"]["gen_ai.request.top_p"] == 1.0

    assert ai_client_span["description"] == "chat gpt-4"
    assert ai_client_span["data"]["gen_ai.operation.name"] == "chat"
    assert ai_client_span["data"]["gen_ai.system"] == "openai"
    assert ai_client_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert ai_client_span["data"]["gen_ai.request.max_tokens"] == 100
    assert ai_client_span["data"]["gen_ai.request.model"] == "gpt-4"
    assert ai_client_span["data"]["gen_ai.request.temperature"] == 0.7
    assert ai_client_span["data"]["gen_ai.request.top_p"] == 1.0


@pytest.mark.asyncio
async def test_tool_execution_span(sentry_init, capture_events, test_agent):
    """Test tool execution span creation."""
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    # Create a simple test tool
    from agents import function_tool

    @function_tool
    def simple_test_tool(message: str) -> str:
        """A simple test tool that returns a message."""
        return f"Tool executed with: {message}"

    # Create agent with the tool
    agent_with_tool = test_agent.clone(tools=[simple_test_tool])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Create a mock response that includes tool calls
            from agents.items import ResponseFunctionToolCall

            tool_call = ResponseFunctionToolCall(
                id="call_123",
                call_id="call_123",
                name="simple_test_tool",
                type="function_call",
                arguments='{"message": "hello"}',
                function=MagicMock(
                    name="simple_test_tool", arguments='{"message": "hello"}'
                ),
            )

            # First response with tool call
            tool_response = ModelResponse(
                output=[tool_call],
                usage=Usage(
                    requests=1, input_tokens=10, output_tokens=5, total_tokens=15
                ),
                response_id="resp_tool_123",
            )

            # Second response with final answer
            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="Task completed using the tool",
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                    )
                ],
                usage=Usage(
                    requests=1, input_tokens=15, output_tokens=10, total_tokens=25
                ),
                response_id="resp_final_123",
            )

            # Return different responses on successive calls
            mock_get_response.side_effect = [tool_response, final_response]

            with sentry_sdk.start_transaction(name="test_transaction"):
                await agents.Runner.run(
                    agent_with_tool, "Please use the simple test tool"
                )

    # Verify spans were created
    assert len(events) > 0
    transaction = events[0]
    spans = transaction.get("spans", [])

    # Should have workflow span
    workflow_spans = [
        span for span in spans if "workflow" in span.get("description", "")
    ]
    assert len(workflow_spans) > 0

    # Should have AI client spans
    ai_spans = [span for span in spans if span.get("op") == "llm.request"]
    assert len(ai_spans) > 0

    # Look for tool execution spans
    tool_spans = [span for span in spans if span.get("op") == "agent.tool"]
    assert len(tool_spans) > 0

    tool_span = tool_spans[0]
    tool_data = tool_span.get("data", {})
    assert tool_data.get("gen_ai.tool.name") == "simple_test_tool"


@pytest.mark.asyncio
async def test_error_handling(sentry_init, capture_events, test_agent):
    """Test error handling in agent execution."""
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Make the model call fail
            mock_get_response.side_effect = Exception("Model Error")

            with sentry_sdk.start_transaction(name="test_transaction"):
                with pytest.raises(Exception, match="Model Error"):
                    await agents.Runner.run(test_agent, "Test input")

    # Verify spans were created even with errors
    if events:
        transaction = events[0]
        spans = transaction.get("spans", [])

        # Should have workflow span
        workflow_spans = [
            span for span in spans if "workflow" in span.get("description", "")
        ]
        assert len(workflow_spans) > 0

        # Check for error spans
        error_spans = [span for span in spans if span.get("status") == "internal_error"]
        assert len(error_spans) > 0


@pytest.mark.asyncio
async def test_integration_with_hooks(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """Test that the integration properly wraps hooks."""
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    # Create a simple hook to test with
    hook_calls = []

    from agents.lifecycle import AgentHooks

    class TestHooks(AgentHooks):
        async def on_agent_start(self, context, agent):
            hook_calls.append("start")

        async def on_agent_end(self, context, agent, output):
            hook_calls.append("end")

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            mock_get_response.return_value = mock_model_response

            with sentry_sdk.start_transaction(name="test_transaction"):
                await agents.Runner.run(test_agent, "Test input", hooks=TestHooks())

    # Verify hooks were called (integration shouldn't break them)
    assert "start" in hook_calls
    assert "end" in hook_calls

    # Verify spans were still created
    assert len(events) > 0
    transaction = events[0]
    spans = transaction.get("spans", [])

    workflow_spans = [
        span for span in spans if "workflow" in span.get("description", "")
    ]
    assert len(workflow_spans) > 0

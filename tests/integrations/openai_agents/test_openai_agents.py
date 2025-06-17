from unittest import mock
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
    """
    Test that the integration creates spans for agent invocations.
    """

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
    """
    Test tool execution span creation.
    """

    @agents.function_tool
    def simple_test_tool(message: str) -> str:
        """A simple tool"""
        return f"Tool executed with: {message}"

    # Create agent with the tool
    agent_with_tool = test_agent.clone(tools=[simple_test_tool])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Create a mock response that includes tool calls
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

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            with sentry_sdk.start_transaction(name="test_transaction"):
                await agents.Runner.run(
                    agent_with_tool, "Please use the simple test tool"
                )

    (transaction,) = events
    spans = transaction["spans"]
    (
        agent_workflow_span,
        agent_span,
        ai_client_span1,
        tool_span,
        ai_client_span2,
    ) = spans

    available_tools = [
        {
            "name": "simple_test_tool",
            "description": "A simple tool",
            "params_json_schema": {
                "properties": {"message": {"title": "Message", "type": "string"}},
                "required": ["message"],
                "title": "simple_test_tool_args",
                "type": "object",
                "additionalProperties": False,
            },
            "on_invoke_tool": mock.ANY,
            "strict_json_schema": True,
            "is_enabled": True,
        }
    ]

    assert agent_workflow_span["description"] == "test_agent workflow"
    assert agent_workflow_span["origin"] == "auto.ai.openai_agents"

    assert agent_span["description"] == "invoke_agent test_agent"
    assert agent_span["origin"] == "auto.ai.openai_agents"
    assert agent_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert agent_span["data"]["gen_ai.operation.name"] == "invoke_agent"
    assert agent_span["data"]["gen_ai.request.available_tools"] == available_tools
    assert agent_span["data"]["gen_ai.request.max_tokens"] == 100
    assert agent_span["data"]["gen_ai.request.model"] == "gpt-4"
    assert agent_span["data"]["gen_ai.request.temperature"] == 0.7
    assert agent_span["data"]["gen_ai.request.top_p"] == 1.0
    assert agent_span["data"]["gen_ai.system"] == "openai"

    assert ai_client_span1["description"] == "chat gpt-4"
    assert ai_client_span1["tags"]["status"] == "internal_error"
    assert ai_client_span1["data"]["gen_ai.operation.name"] == "chat"
    assert ai_client_span1["data"]["gen_ai.system"] == "openai"
    assert ai_client_span1["data"]["gen_ai.agent.name"] == "test_agent"
    assert ai_client_span1["data"]["gen_ai.request.available_tools"] == available_tools
    assert ai_client_span1["data"]["gen_ai.request.max_tokens"] == 100
    assert ai_client_span1["data"]["gen_ai.request.messages"] == [
        {
            "role": "system",
            "content": [{"type": "text", "text": "You are a helpful test assistant."}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "Please use the simple test tool"}],
        },
    ]
    assert ai_client_span1["data"]["gen_ai.request.model"] == "gpt-4"
    assert ai_client_span1["data"]["gen_ai.request.temperature"] == 0.7
    assert ai_client_span1["data"]["gen_ai.request.top_p"] == 1.0
    assert ai_client_span1["data"]["gen_ai.usage.input_tokens"] == 10
    assert ai_client_span1["data"]["gen_ai.usage.input_tokens.cached"] == 0
    assert ai_client_span1["data"]["gen_ai.usage.output_tokens"] == 5
    assert ai_client_span1["data"]["gen_ai.usage.output_tokens.reasoning"] == 0
    assert ai_client_span1["data"]["gen_ai.usage.total_tokens"] == 15
    assert ai_client_span1["data"]["gen_ai.response.tool_calls"] == [
        {
            "arguments": '{"message": "hello"}',
            "call_id": "call_123",
            "name": "simple_test_tool",
            "type": "function_call",
            "id": "call_123",
            "status": None,
            "function": mock.ANY,
        }
    ]

    assert tool_span["description"] == "execute_tool simple_test_tool"
    assert tool_span["tags"]["status"] == "internal_error"
    assert tool_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert tool_span["data"]["gen_ai.operation.name"] == "execute_tool"
    assert tool_span["data"]["gen_ai.request.available_tools"] == available_tools
    assert tool_span["data"]["gen_ai.request.max_tokens"] == 100
    assert tool_span["data"]["gen_ai.request.model"] == "gpt-4"
    assert tool_span["data"]["gen_ai.request.temperature"] == 0.7
    assert tool_span["data"]["gen_ai.request.top_p"] == 1.0
    assert tool_span["data"]["gen_ai.system"] == "openai"
    assert tool_span["data"]["gen_ai.tool.description"] == "A simple tool"
    assert tool_span["data"]["gen_ai.tool.input"] == '{"message": "hello"}'
    assert tool_span["data"]["gen_ai.tool.name"] == "simple_test_tool"
    assert tool_span["data"]["gen_ai.tool.output"] == "Tool executed with: hello"
    assert tool_span["data"]["gen_ai.tool.type"] == "function"

    assert ai_client_span2["description"] == "chat gpt-4"
    assert ai_client_span2["tags"]["status"] == "internal_error"
    assert ai_client_span2["data"]["gen_ai.agent.name"] == "test_agent"
    assert ai_client_span2["data"]["gen_ai.operation.name"] == "chat"
    assert ai_client_span2["data"]["gen_ai.request.available_tools"] == available_tools
    assert ai_client_span2["data"]["gen_ai.request.max_tokens"] == 100
    assert ai_client_span2["data"]["gen_ai.request.messages"] == [
        {
            "role": "system",
            "content": [{"type": "text", "text": "You are a helpful test assistant."}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "Please use the simple test tool"}],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "arguments": '{"message": "hello"}',
                    "call_id": "call_123",
                    "name": "simple_test_tool",
                    "type": "function_call",
                    "id": "call_123",
                    "function": mock.ANY,
                }
            ],
        },
        {
            "role": "tool",
            "content": [
                {
                    "call_id": "call_123",
                    "output": "Tool executed with: hello",
                    "type": "function_call_output",
                }
            ],
        },
    ]
    assert ai_client_span2["data"]["gen_ai.request.model"] == "gpt-4"
    assert ai_client_span2["data"]["gen_ai.request.temperature"] == 0.7
    assert ai_client_span2["data"]["gen_ai.request.top_p"] == 1.0
    assert ai_client_span2["data"]["gen_ai.response.text"] == [
        "Task completed using the tool"
    ]
    assert ai_client_span2["data"]["gen_ai.system"] == "openai"
    assert ai_client_span2["data"]["gen_ai.usage.input_tokens.cached"] == 0
    assert ai_client_span2["data"]["gen_ai.usage.input_tokens"] == 15
    assert ai_client_span2["data"]["gen_ai.usage.output_tokens.reasoning"] == 0
    assert ai_client_span2["data"]["gen_ai.usage.output_tokens"] == 10
    assert ai_client_span2["data"]["gen_ai.usage.total_tokens"] == 25


@pytest.mark.asyncio
async def test_error_handling(sentry_init, capture_events, test_agent):
    """
    Test error handling in agent execution.
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            mock_get_response.side_effect = Exception("Model Error")

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            with sentry_sdk.start_transaction(name="test_transaction"):
                with pytest.raises(Exception, match="Model Error"):
                    await agents.Runner.run(test_agent, "Test input")

    (transaction,) = events
    spans = transaction["spans"]
    (agent_workflow_span,) = spans

    assert agent_workflow_span["description"] == "test_agent workflow"
    assert agent_workflow_span["origin"] == "auto.ai.openai_agents"
    assert agent_workflow_span["tags"]["status"] == "internal_error"


@pytest.mark.asyncio
async def test_integration_with_hooks(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """
    Test that the integration properly wraps hooks.
    """

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

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            with sentry_sdk.start_transaction(name="test_transaction"):
                await agents.Runner.run(test_agent, "Test input", hooks=TestHooks())

    # Verify hooks were called (integration shouldn't break them)
    assert "start" in hook_calls
    assert "end" in hook_calls

    # Verify spans were still created
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

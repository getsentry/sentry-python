import asyncio
import json
import os
import re
from unittest.mock import MagicMock, patch

import agents
import pytest
from agents import (
    Agent,
    ModelResponse,
    ModelSettings,
    Usage,
)
from agents.items import (
    McpCall,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from agents.version import __version__ as OPENAI_AGENTS_VERSION
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
)

import sentry_sdk
from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration
from sentry_sdk.integrations.openai_agents.utils import safe_serialize
from sentry_sdk.utils import parse_version

test_run_config = agents.RunConfig(tracing_disabled=True)


@pytest.fixture
def mock_usage():
    return Usage(
        requests=1,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=5),
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


@pytest.fixture
def test_agent_custom_model():
    """Create a real Agent instance for testing."""
    return Agent(
        name="test_agent_custom_model",
        instructions="You are a helpful test assistant.",
        # the model could be agents.OpenAIChatCompletionsModel()
        model="my-custom-model",
        model_settings=ModelSettings(
            max_tokens=100,
            temperature=0.7,
            top_p=1.0,
            presence_penalty=0.0,
            frequency_penalty=0.0,
        ),
    )


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
                send_default_pii=True,
            )

            events = capture_events()

            result = await agents.Runner.run(
                test_agent, "Test input", run_config=test_run_config
            )

            assert result is not None
            assert result.final_output == "Hello, how can I help you?"

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span, ai_client_span = spans

    assert transaction["transaction"] == "test_agent workflow"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.openai_agents"

    assert invoke_agent_span["description"] == "invoke_agent test_agent"
    assert invoke_agent_span["data"]["gen_ai.request.messages"] == safe_serialize(
        [
            {
                "content": [
                    {"text": "You are a helpful test assistant.", "type": "text"}
                ],
                "role": "system",
            },
            {"content": [{"text": "Test input", "type": "text"}], "role": "user"},
        ]
    )
    assert (
        invoke_agent_span["data"]["gen_ai.response.text"]
        == "Hello, how can I help you?"
    )
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
async def test_client_span_custom_model(
    sentry_init, capture_events, test_agent_custom_model, mock_model_response
):
    """
    Test that the integration uses the correct model name if a custom model is used.
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

            result = await agents.Runner.run(
                test_agent_custom_model, "Test input", run_config=test_run_config
            )

            assert result is not None
            assert result.final_output == "Hello, how can I help you?"

    (transaction,) = events
    spans = transaction["spans"]
    _, ai_client_span = spans

    assert ai_client_span["description"] == "chat my-custom-model"
    assert ai_client_span["data"]["gen_ai.request.model"] == "my-custom-model"


def test_agent_invocation_span_sync(
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

            result = agents.Runner.run_sync(
                test_agent, "Test input", run_config=test_run_config
            )

            assert result is not None
            assert result.final_output == "Hello, how can I help you?"

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span, ai_client_span = spans

    assert transaction["transaction"] == "test_agent workflow"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.openai_agents"

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
async def test_handoff_span(sentry_init, capture_events, mock_usage):
    """
    Test that handoff spans are created when agents hand off to other agents.
    """
    # Create two simple agents with a handoff relationship
    secondary_agent = agents.Agent(
        name="secondary_agent",
        instructions="You are a secondary agent.",
        model="gpt-4o-mini",
    )

    primary_agent = agents.Agent(
        name="primary_agent",
        instructions="You are a primary agent that hands off to secondary agent.",
        model="gpt-4o-mini",
        handoffs=[secondary_agent],
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Mock two responses:
            # 1. Primary agent calls handoff tool
            # 2. Secondary agent provides final response
            handoff_response = ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        id="call_handoff_123",
                        call_id="call_handoff_123",
                        name="transfer_to_secondary_agent",
                        type="function_call",
                        arguments="{}",
                        function=MagicMock(
                            name="transfer_to_secondary_agent", arguments="{}"
                        ),
                    )
                ],
                usage=mock_usage,
                response_id="resp_handoff_123",
            )

            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="I'm the specialist and I can help with that!",
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                    )
                ],
                usage=mock_usage,
                response_id="resp_final_123",
            )

            mock_get_response.side_effect = [handoff_response, final_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            result = await agents.Runner.run(
                primary_agent,
                "Please hand off to secondary agent",
                run_config=test_run_config,
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    handoff_span = spans[2]

    # Verify handoff span was created
    assert handoff_span is not None
    assert (
        handoff_span["description"] == "handoff from primary_agent to secondary_agent"
    )
    assert handoff_span["data"]["gen_ai.operation.name"] == "handoff"


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

            await agents.Runner.run(
                agent_with_tool,
                "Please use the simple test tool",
                run_config=test_run_config,
            )

    (transaction,) = events
    spans = transaction["spans"]
    (
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
            "on_invoke_tool": "<function agents.tool.function_tool.<locals>._create_function_tool.<locals>._on_invoke_tool>",
            "strict_json_schema": True,
            "is_enabled": True,
        }
    ]
    if parse_version(OPENAI_AGENTS_VERSION) >= (0, 3, 3):
        available_tools[0].update(
            {"tool_input_guardrails": None, "tool_output_guardrails": None}
        )

    available_tools = safe_serialize(available_tools)

    assert transaction["transaction"] == "test_agent workflow"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.openai_agents"

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
    assert ai_client_span1["data"]["gen_ai.operation.name"] == "chat"
    assert ai_client_span1["data"]["gen_ai.system"] == "openai"
    assert ai_client_span1["data"]["gen_ai.agent.name"] == "test_agent"
    assert ai_client_span1["data"]["gen_ai.request.available_tools"] == available_tools
    assert ai_client_span1["data"]["gen_ai.request.max_tokens"] == 100
    assert ai_client_span1["data"]["gen_ai.request.messages"] == safe_serialize(
        [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "You are a helpful test assistant."}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please use the simple test tool"}
                ],
            },
        ]
    )
    assert ai_client_span1["data"]["gen_ai.request.model"] == "gpt-4"
    assert ai_client_span1["data"]["gen_ai.request.temperature"] == 0.7
    assert ai_client_span1["data"]["gen_ai.request.top_p"] == 1.0
    assert ai_client_span1["data"]["gen_ai.usage.input_tokens"] == 10
    assert ai_client_span1["data"]["gen_ai.usage.input_tokens.cached"] == 0
    assert ai_client_span1["data"]["gen_ai.usage.output_tokens"] == 5
    assert ai_client_span1["data"]["gen_ai.usage.output_tokens.reasoning"] == 0
    assert ai_client_span1["data"]["gen_ai.usage.total_tokens"] == 15
    assert re.sub(
        r"SerializationIterator\(.*\)",
        "NOT_CHECKED",
        ai_client_span1["data"]["gen_ai.response.tool_calls"],
    ) == safe_serialize(
        [
            {
                "arguments": '{"message": "hello"}',
                "call_id": "call_123",
                "name": "simple_test_tool",
                "type": "function_call",
                "id": "call_123",
                "status": None,
                "function": "NOT_CHECKED",
            }
        ]
    )

    assert tool_span["description"] == "execute_tool simple_test_tool"
    assert tool_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert tool_span["data"]["gen_ai.operation.name"] == "execute_tool"
    assert (
        re.sub(
            "<.*>(,)",
            r"'NOT_CHECKED'\1",
            agent_span["data"]["gen_ai.request.available_tools"],
        )
        == available_tools
    )
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
    assert ai_client_span2["data"]["gen_ai.agent.name"] == "test_agent"
    assert ai_client_span2["data"]["gen_ai.operation.name"] == "chat"
    assert (
        re.sub(
            "<.*>(,)",
            r"'NOT_CHECKED'\1",
            agent_span["data"]["gen_ai.request.available_tools"],
        )
        == available_tools
    )
    assert ai_client_span2["data"]["gen_ai.request.max_tokens"] == 100
    assert re.sub(
        r"SerializationIterator\(.*\)",
        "NOT_CHECKED",
        ai_client_span2["data"]["gen_ai.request.messages"],
    ) == safe_serialize(
        [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "You are a helpful test assistant."}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please use the simple test tool"}
                ],
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
                        "function": "NOT_CHECKED",
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
    )
    assert ai_client_span2["data"]["gen_ai.request.model"] == "gpt-4"
    assert ai_client_span2["data"]["gen_ai.request.temperature"] == 0.7
    assert ai_client_span2["data"]["gen_ai.request.top_p"] == 1.0
    assert (
        ai_client_span2["data"]["gen_ai.response.text"]
        == "Task completed using the tool"
    )
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

            with pytest.raises(Exception, match="Model Error"):
                await agents.Runner.run(
                    test_agent, "Test input", run_config=test_run_config
                )

    (
        error_event,
        transaction,
    ) = events

    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert error_event["exception"]["values"][0]["value"] == "Model Error"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "openai_agents"

    spans = transaction["spans"]
    (invoke_agent_span, ai_client_span) = spans

    assert transaction["transaction"] == "test_agent workflow"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.openai_agents"

    assert invoke_agent_span["description"] == "invoke_agent test_agent"
    assert invoke_agent_span["origin"] == "auto.ai.openai_agents"

    assert ai_client_span["description"] == "chat gpt-4"
    assert ai_client_span["origin"] == "auto.ai.openai_agents"
    assert ai_client_span["tags"]["status"] == "internal_error"


@pytest.mark.asyncio
async def test_span_status_error(sentry_init, capture_events, test_agent):
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            mock_get_response.side_effect = ValueError("Model Error")

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            with pytest.raises(ValueError, match="Model Error"):
                await agents.Runner.run(
                    test_agent, "Test input", run_config=test_run_config
                )

    (error, transaction) = events
    assert error["level"] == "error"
    assert transaction["spans"][0]["tags"]["status"] == "error"
    assert transaction["contexts"]["trace"]["status"] == "error"


@pytest.mark.asyncio
async def test_mcp_tool_execution_spans(sentry_init, capture_events, test_agent):
    """
    Test that MCP (Model Context Protocol) tool calls create execute_tool spans.
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Create a McpCall object
            mcp_call = McpCall(
                id="mcp_call_123",
                name="test_mcp_tool",
                arguments='{"query": "search term"}',
                output="MCP tool executed successfully",
                error=None,
                type="mcp_call",
                server_label="test_server",
            )

            # Create a ModelResponse with an McpCall in the output
            mcp_response = ModelResponse(
                output=[mcp_call],
                usage=Usage(
                    requests=1,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
                response_id="resp_mcp_123",
            )

            # Final response after MCP tool execution
            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="Task completed using MCP tool",
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                    )
                ],
                usage=Usage(
                    requests=1,
                    input_tokens=15,
                    output_tokens=10,
                    total_tokens=25,
                ),
                response_id="resp_final_123",
            )

            mock_get_response.side_effect = [mcp_response, final_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            await agents.Runner.run(
                test_agent,
                "Please use MCP tool",
                run_config=test_run_config,
            )

    (transaction,) = events
    spans = transaction["spans"]

    # Find the MCP execute_tool span
    mcp_tool_span = None
    for span in spans:
        if (
            span.get("description") == "execute_tool test_mcp_tool"
            and span.get("data", {}).get("gen_ai.tool.type") == "mcp"
        ):
            mcp_tool_span = span
            break

    # Verify the MCP tool span was created
    assert mcp_tool_span is not None, "MCP execute_tool span was not created"
    assert mcp_tool_span["description"] == "execute_tool test_mcp_tool"
    assert mcp_tool_span["data"]["gen_ai.tool.type"] == "mcp"
    assert mcp_tool_span["data"]["gen_ai.tool.name"] == "test_mcp_tool"
    assert mcp_tool_span["data"]["gen_ai.tool.input"] == '{"query": "search term"}'
    assert (
        mcp_tool_span["data"]["gen_ai.tool.output"] == "MCP tool executed successfully"
    )

    # Verify no error status since error was None
    assert mcp_tool_span.get("tags", {}).get("status") != "error"


@pytest.mark.asyncio
async def test_mcp_tool_execution_with_error(sentry_init, capture_events, test_agent):
    """
    Test that MCP tool calls with errors are tracked with error status.
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Create a McpCall object with an error
            mcp_call_with_error = McpCall(
                id="mcp_call_error_123",
                name="failing_mcp_tool",
                arguments='{"query": "test"}',
                output=None,
                error="MCP tool execution failed",
                type="mcp_call",
                server_label="test_server",
            )

            # Create a ModelResponse with a failing McpCall
            mcp_response = ModelResponse(
                output=[mcp_call_with_error],
                usage=Usage(
                    requests=1,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
                response_id="resp_mcp_error_123",
            )

            # Final response after error
            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="The MCP tool encountered an error",
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                    )
                ],
                usage=Usage(
                    requests=1,
                    input_tokens=15,
                    output_tokens=10,
                    total_tokens=25,
                ),
                response_id="resp_final_error_123",
            )

            mock_get_response.side_effect = [mcp_response, final_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            await agents.Runner.run(
                test_agent,
                "Please use failing MCP tool",
                run_config=test_run_config,
            )

    (transaction,) = events
    spans = transaction["spans"]

    # Find the MCP execute_tool span with error
    mcp_tool_span = None
    for span in spans:
        if (
            span.get("description") == "execute_tool failing_mcp_tool"
            and span.get("data", {}).get("gen_ai.tool.type") == "mcp"
        ):
            mcp_tool_span = span
            break

    # Verify the MCP tool span was created with error status
    assert mcp_tool_span is not None, "MCP execute_tool span was not created"
    assert mcp_tool_span["description"] == "execute_tool failing_mcp_tool"
    assert mcp_tool_span["data"]["gen_ai.tool.type"] == "mcp"
    assert mcp_tool_span["data"]["gen_ai.tool.name"] == "failing_mcp_tool"
    assert mcp_tool_span["data"]["gen_ai.tool.input"] == '{"query": "test"}'
    assert mcp_tool_span["data"]["gen_ai.tool.output"] is None

    # Verify error status was set
    assert mcp_tool_span["tags"]["status"] == "error"


@pytest.mark.asyncio
async def test_mcp_tool_execution_without_pii(sentry_init, capture_events, test_agent):
    """
    Test that MCP tool input/output are not included when send_default_pii is False.
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Create a McpCall object
            mcp_call = McpCall(
                id="mcp_call_pii_123",
                name="test_mcp_tool",
                arguments='{"query": "sensitive data"}',
                output="Result with sensitive info",
                error=None,
                type="mcp_call",
                server_label="test_server",
            )

            # Create a ModelResponse with an McpCall
            mcp_response = ModelResponse(
                output=[mcp_call],
                usage=Usage(
                    requests=1,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
                response_id="resp_mcp_123",
            )

            # Final response
            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="Task completed",
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                    )
                ],
                usage=Usage(
                    requests=1,
                    input_tokens=15,
                    output_tokens=10,
                    total_tokens=25,
                ),
                response_id="resp_final_123",
            )

            mock_get_response.side_effect = [mcp_response, final_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=False,  # PII disabled
            )

            events = capture_events()

            await agents.Runner.run(
                test_agent,
                "Please use MCP tool",
                run_config=test_run_config,
            )

    (transaction,) = events
    spans = transaction["spans"]

    # Find the MCP execute_tool span
    mcp_tool_span = None
    for span in spans:
        if (
            span.get("description") == "execute_tool test_mcp_tool"
            and span.get("data", {}).get("gen_ai.tool.type") == "mcp"
        ):
            mcp_tool_span = span
            break

    # Verify the MCP tool span was created but without input/output
    assert mcp_tool_span is not None, "MCP execute_tool span was not created"
    assert mcp_tool_span["description"] == "execute_tool test_mcp_tool"
    assert mcp_tool_span["data"]["gen_ai.tool.type"] == "mcp"
    assert mcp_tool_span["data"]["gen_ai.tool.name"] == "test_mcp_tool"

    # Verify input and output are not included when send_default_pii is False
    assert "gen_ai.tool.input" not in mcp_tool_span["data"]
    assert "gen_ai.tool.output" not in mcp_tool_span["data"]


@pytest.mark.asyncio
async def test_multiple_agents_asyncio(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """
    Test that multiple agents can be run at the same time in asyncio tasks
    without interfering with each other.
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

            async def run():
                await agents.Runner.run(
                    starting_agent=test_agent,
                    input="Test input",
                    run_config=test_run_config,
                )

            await asyncio.gather(*[run() for _ in range(3)])

    assert len(events) == 3
    txn1, txn2, txn3 = events

    assert txn1["type"] == "transaction"
    assert txn1["transaction"] == "test_agent workflow"
    assert txn2["type"] == "transaction"
    assert txn2["transaction"] == "test_agent workflow"
    assert txn3["type"] == "transaction"
    assert txn3["transaction"] == "test_agent workflow"


def test_openai_agents_message_role_mapping(sentry_init, capture_events):
    """Test that OpenAI Agents integration properly maps message roles like 'ai' to 'assistant'"""
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    # Test input messages with mixed roles including "ai"
    test_input = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "ai", "content": "Hi there!"},  # Should be mapped to "assistant"
        {"role": "assistant", "content": "How can I help?"},  # Should stay "assistant"
    ]

    get_response_kwargs = {"input": test_input}

    from sentry_sdk import start_span
    from sentry_sdk.integrations.openai_agents.utils import _set_input_data

    with start_span(op="test") as span:
        _set_input_data(span, get_response_kwargs)

    # Verify that messages were processed and roles were mapped
    from sentry_sdk.consts import SPANDATA

    if SPANDATA.GEN_AI_REQUEST_MESSAGES in span._data:
        stored_messages = json.loads(span._data[SPANDATA.GEN_AI_REQUEST_MESSAGES])

        # Verify roles were properly mapped
        found_assistant_roles = 0
        for message in stored_messages:
            if message["role"] == "assistant":
                found_assistant_roles += 1

        # Should have 2 assistant roles (1 from original "assistant", 1 from mapped "ai")
        assert found_assistant_roles == 2

        # Verify no "ai" roles remain in any message
        for message in stored_messages:
            assert message["role"] != "ai"


@pytest.mark.asyncio
async def test_openai_agents_message_truncation(
    sentry_init, capture_events, test_agent, mock_usage
):
    """Test that large messages are truncated properly in OpenAI Agents integration."""

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            large_content = (
                "This is a very long message that will exceed our size limits. " * 1000
            )

            large_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_large",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text=large_content,
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                    )
                ],
                usage=mock_usage,
                response_id="resp_large",
            )

            mock_get_response.return_value = large_response

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            # Create messages with mixed large/small content by patching get_response
            with patch(
                "agents.models.openai_responses.OpenAIResponsesModel.get_response"
            ) as mock_inner:
                mock_inner.side_effect = [large_response] * 5

                # We'll test with the agent itself, not the messages
                # since OpenAI agents tracks messages internally
                result = await agents.Runner.run(
                    test_agent, "Test input", run_config=test_run_config
                )

            assert result is not None

    assert len(events) > 0
    tx = events[0]
    assert tx["type"] == "transaction"

    # Check ai_client spans (these have the truncation)
    ai_client_spans = [
        span for span in tx.get("spans", []) if span.get("op") == "gen_ai.chat"
    ]
    assert len(ai_client_spans) > 0

    # Just verify that messages are being set and truncation is applied
    # The actual truncation behavior is tested in the ai_monitoring tests
    ai_client_span = ai_client_spans[0]
    if "gen_ai.request.messages" in ai_client_span["data"]:
        messages_data = ai_client_span["data"]["gen_ai.request.messages"]
        assert isinstance(messages_data, str)

        parsed_messages = json.loads(messages_data)
        assert isinstance(parsed_messages, list)
        # Verify messages were processed
        assert len(parsed_messages) >= 1


@pytest.mark.asyncio
async def test_tool_execution_error_tracing(sentry_init, capture_events, test_agent):
    """
    Test that tool execution errors are properly tracked via error tracing patch.

    This tests the patch of agents error tracing function to ensure execute_tool
    spans are set to error status when tool execution fails.

    The function location varies by version:
    - Newer versions: agents.util._error_tracing.attach_error_to_current_span
    - Older versions: agents._utils.attach_error_to_current_span
    """

    @agents.function_tool
    def failing_tool(message: str) -> str:
        """A tool that fails"""
        raise ValueError("Tool execution failed")

    agent_with_tool = test_agent.clone(tools=[failing_tool])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            tool_call = ResponseFunctionToolCall(
                id="call_123",
                call_id="call_123",
                name="failing_tool",
                type="function_call",
                arguments='{"message": "test"}',
                function=MagicMock(
                    name="failing_tool", arguments='{"message": "test"}'
                ),
            )

            tool_response = ModelResponse(
                output=[tool_call],
                usage=Usage(
                    requests=1, input_tokens=10, output_tokens=5, total_tokens=15
                ),
                response_id="resp_tool_123",
            )

            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="An error occurred while running the tool",
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

            mock_get_response.side_effect = [tool_response, final_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            await agents.Runner.run(
                agent_with_tool,
                "Please use the failing tool",
                run_config=test_run_config,
            )

    (transaction,) = events
    spans = transaction["spans"]

    execute_tool_span = None
    for span in spans:
        if span.get("description", "").startswith("execute_tool failing_tool"):
            execute_tool_span = span
            break

    assert execute_tool_span is not None, "execute_tool span was not created"
    assert execute_tool_span["description"] == "execute_tool failing_tool"
    assert execute_tool_span["data"]["gen_ai.tool.name"] == "failing_tool"
    assert execute_tool_span["tags"]["status"] == "error"


def test_openai_agents_message_truncation(sentry_init, capture_events):
    """Test that large messages are truncated properly in OpenAI Agents integration."""
    from sentry_sdk import start_span
    from sentry_sdk.consts import SPANDATA
    from sentry_sdk.integrations.openai_agents.utils import _set_input_data

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )

    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    test_messages = [
        {"role": "system", "content": "small message 1"},
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": large_content},
        {"role": "user", "content": "small message 4"},
        {"role": "assistant", "content": "small message 5"},
    ]

    get_response_kwargs = {"input": test_messages}

    with start_span(op="gen_ai.chat") as span:
        scope = sentry_sdk.get_current_scope()
        _set_input_data(span, get_response_kwargs)
        # Assert that annotation for truncation metadata is present when messages are truncated
        if hasattr(scope, "_gen_ai_original_message_count"):
            truncated_count = scope._gen_ai_original_message_count.get(span.span_id)
            assert truncated_count == 5, (
                f"Expected 5 original messages, got {truncated_count}"
            )

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span._data
    messages_data = span._data[SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) == 2
    assert "small message 4" in str(parsed_messages[0])
    assert "small message 5" in str(parsed_messages[1])

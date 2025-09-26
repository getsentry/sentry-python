import asyncio
import re
import pytest
from unittest.mock import MagicMock, patch
import os

from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration
from sentry_sdk.integrations.openai_agents.utils import safe_serialize

import agents
from agents import (
    Agent,
    ModelResponse,
    Usage,
    ModelSettings,
)
from agents.items import (
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseFunctionToolCall,
)

from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
)

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

    # Expect simplified tool format, not raw tool data
    available_tools = [
        {"name": "simple_test_tool", "description": "A simple tool", "type": "function"}
    ]

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
    # Tool calls are now stored as a list, not a JSON string
    tool_calls = ai_client_span1["data"]["gen_ai.response.tool_calls"]
    assert len(tool_calls) == 1
    tool_call = tool_calls[0]
    assert tool_call["arguments"] == '{"message": "hello"}'
    assert tool_call["call_id"] == "call_123"
    assert tool_call["name"] == "simple_test_tool"
    assert tool_call["type"] == "function_call"
    assert tool_call["id"] == "call_123"
    assert tool_call["status"] is None
    # Don't check the function field as it contains mock objects

    assert tool_span["description"] == "execute_tool simple_test_tool"
    assert tool_span["data"]["gen_ai.agent.name"] == "test_agent"
    assert tool_span["data"]["gen_ai.operation.name"] == "execute_tool"
    assert agent_span["data"]["gen_ai.request.available_tools"] == available_tools
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
    # available_tools is now a list, not a JSON string, so we can compare directly
    assert agent_span["data"]["gen_ai.request.available_tools"] == [
        {"name": "simple_test_tool", "description": "A simple tool", "type": "function"}
    ]
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


@pytest.mark.asyncio
async def test_available_tools_simplified_format(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """
    Test that available tools are recorded in simplified format on invoke_agent spans.
    """

    @agents.function_tool
    def search_tool(query: str) -> str:
        """Search for information using the given query."""
        return f"Search results for: {query}"

    @agents.function_tool
    def calculator_tool(expression: str) -> str:
        """Calculate mathematical expressions."""
        return f"Result: {expression}"

    # Create agent with multiple tools
    agent_with_tools = test_agent.clone(tools=[search_tool, calculator_tool])

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
                agent_with_tools, "Test input", run_config=test_run_config
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span = spans[0]

    # Verify simplified tools format
    available_tools = invoke_agent_span["data"]["gen_ai.request.available_tools"]
    assert isinstance(available_tools, list)
    assert len(available_tools) == 2

    # Check first tool
    search_tool_data = next(
        (t for t in available_tools if t["name"] == "search_tool"), None
    )
    assert search_tool_data is not None
    assert search_tool_data["name"] == "search_tool"
    assert (
        search_tool_data["description"]
        == "Search for information using the given query."
    )
    assert search_tool_data["type"] == "function"

    # Check second tool
    calculator_tool_data = next(
        (t for t in available_tools if t["name"] == "calculator_tool"), None
    )
    assert calculator_tool_data is not None
    assert calculator_tool_data["name"] == "calculator_tool"
    assert calculator_tool_data["description"] == "Calculate mathematical expressions."
    assert calculator_tool_data["type"] == "function"

    # Verify no extra fields are included (simplified format)
    for tool_data in available_tools:
        expected_keys = {"name", "description", "type"}
        assert set(tool_data.keys()) == expected_keys


@pytest.mark.asyncio
async def test_tool_calls_captured_in_invoke_agent_span(
    sentry_init, capture_events, test_agent
):
    """
    Test that tool calls are captured in invoke_agent spans when tools are used.
    """

    @agents.function_tool
    def test_function(input_text: str) -> str:
        """A test function."""
        return f"Processed: {input_text}"

    agent_with_tool = test_agent.clone(tools=[test_function])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:

            # Mock response that includes a tool call
            tool_call_response = ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        id="call_test_123",
                        call_id="call_test_123",
                        name="test_function",
                        type="function_call",
                        arguments='{"input_text": "hello world"}',
                        function=MagicMock(
                            name="test_function",
                            arguments='{"input_text": "hello world"}',
                        ),
                    )
                ],
                usage=Usage(
                    requests=1, input_tokens=10, output_tokens=5, total_tokens=15
                ),
                response_id="resp_tool_123",
            )

            # Final response after tool execution
            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="Tool execution completed successfully",
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

            mock_get_response.side_effect = [tool_call_response, final_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            result = await agents.Runner.run(
                agent_with_tool,
                "Please use the test function",
                run_config=test_run_config,
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span = spans[0]

    # Verify that available tools are recorded
    assert "gen_ai.request.available_tools" in invoke_agent_span["data"]
    available_tools = invoke_agent_span["data"]["gen_ai.request.available_tools"]
    assert len(available_tools) == 1
    assert available_tools[0]["name"] == "test_function"
    assert available_tools[0]["type"] == "function"

    # Find the AI client span that contains the tool call (first response)
    # The tool calls should be captured in the AI client span, not the invoke agent span
    tool_call_span = None
    for span in spans:
        if span.get("description", "").startswith(
            "chat"
        ) and "gen_ai.response.tool_calls" in span.get("data", {}):
            tool_call_span = span
            break

    assert tool_call_span is not None, "Tool call span not found"
    tool_calls = tool_call_span["data"]["gen_ai.response.tool_calls"]
    assert len(tool_calls) == 1

    tool_call = tool_calls[0]
    assert tool_call["name"] == "test_function"
    assert tool_call["type"] == "function_call"
    assert tool_call["call_id"] == "call_test_123"
    assert tool_call["arguments"] == '{"input_text": "hello world"}'


@pytest.mark.asyncio
async def test_agent_without_tools(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """
    Test that agents without tools don't cause issues and don't include tools data.
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

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span = spans[0]

    # Agent has no tools, so available_tools should not be present
    assert "gen_ai.request.available_tools" not in invoke_agent_span["data"]

    # And no tool calls should be present since no tools were used
    assert "gen_ai.response.tool_calls" not in invoke_agent_span["data"]


def test_simplify_openai_agent_tools_edge_cases():
    """
    Test edge cases for the _simplify_openai_agent_tools function.
    """
    from sentry_sdk.integrations.openai_agents.utils import _simplify_openai_agent_tools

    # Test with None
    assert _simplify_openai_agent_tools(None) is None

    # Test with empty list
    assert _simplify_openai_agent_tools([]) is None

    # Test with non-list/tuple
    assert _simplify_openai_agent_tools("invalid") is None
    assert _simplify_openai_agent_tools(42) is None

    # Test with mock tool objects
    class FunctionTool:
        def __init__(self, name, description):
            self.name = name
            self.description = description

    class CustomTool:
        def __init__(self, name, description):
            self.name = name
            self.description = description

    # Test with valid tools
    mock_tools = [
        FunctionTool("tool1", "Description 1"),
        CustomTool("tool2", "Description 2"),
    ]

    result = _simplify_openai_agent_tools(mock_tools)
    assert result is not None
    assert len(result) == 2
    assert result[0]["name"] == "tool1"
    assert result[0]["description"] == "Description 1"
    assert result[0]["type"] == "function"
    assert result[1]["name"] == "tool2"
    assert result[1]["description"] == "Description 2"
    assert result[1]["type"] == "custom"

    # Test with tool missing name (should be filtered out)
    class MockToolNoName:
        def __init__(self):
            self.description = "Has description but no name"

    mock_tools_with_invalid = [
        FunctionTool("valid_tool", "Valid description"),
        MockToolNoName(),
    ]

    result = _simplify_openai_agent_tools(mock_tools_with_invalid)
    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "valid_tool"

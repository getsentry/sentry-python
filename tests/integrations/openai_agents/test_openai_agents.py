import asyncio
import re
import pytest
from unittest.mock import MagicMock, patch
import os
import json

import sentry_sdk
from sentry_sdk import start_span
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration
from sentry_sdk.integrations.openai_agents.utils import _set_input_data, safe_serialize
from sentry_sdk.utils import parse_version

from openai import AsyncOpenAI
from agents.models.openai_responses import OpenAIResponsesModel

from unittest import mock
from unittest.mock import AsyncMock

import agents
from agents import (
    Agent,
    ModelResponse,
    Usage,
    ModelSettings,
)
from agents.items import (
    McpCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseFunctionToolCall,
)
from agents.tool import HostedMCPTool
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.version import __version__ as OPENAI_AGENTS_VERSION

from openai.types.responses import (
    ResponseCreatedEvent,
    ResponseTextDeltaEvent,
    ResponseCompletedEvent,
    Response,
    ResponseUsage,
)
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
)

test_run_config = agents.RunConfig(tracing_disabled=True)

EXAMPLE_RESPONSE = Response(
    id="chat-id",
    output=[
        ResponseOutputMessage(
            id="message-id",
            content=[
                ResponseOutputText(
                    annotations=[],
                    text="the model response",
                    type="output_text",
                ),
            ],
            role="assistant",
            status="completed",
            type="message",
        ),
    ],
    parallel_tool_calls=False,
    tool_choice="none",
    tools=[],
    created_at=10000000,
    model="response-model-id",
    object="response",
    usage=ResponseUsage(
        input_tokens=20,
        input_tokens_details=InputTokensDetails(
            cached_tokens=5,
        ),
        output_tokens=10,
        output_tokens_details=OutputTokensDetails(
            reasoning_tokens=8,
        ),
        total_tokens=30,
    ),
)


async def EXAMPLE_STREAMED_RESPONSE(*args, **kwargs):
    yield ResponseCreatedEvent(
        response=Response(
            id="chat-id",
            output=[],
            parallel_tool_calls=False,
            tool_choice="none",
            tools=[],
            created_at=10000000,
            model="response-model-id",
            object="response",
        ),
        type="response.created",
        sequence_number=0,
    )

    yield ResponseCompletedEvent(
        response=Response(
            id="chat-id",
            output=[
                ResponseOutputMessage(
                    id="message-id",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="the model response",
                            type="output_text",
                        ),
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                ),
            ],
            parallel_tool_calls=False,
            tool_choice="none",
            tools=[],
            created_at=10000000,
            model="response-model-id",
            object="response",
            usage=ResponseUsage(
                input_tokens=20,
                input_tokens_details=InputTokensDetails(
                    cached_tokens=5,
                ),
                output_tokens=10,
                output_tokens_details=OutputTokensDetails(
                    reasoning_tokens=8,
                ),
                total_tokens=30,
            ),
        ),
        type="response.completed",
        sequence_number=1,
    )


async def EXAMPLE_STREAMED_RESPONSE_WITH_DELTA(*args, **kwargs):
    yield ResponseCreatedEvent(
        response=Response(
            id="chat-id",
            output=[],
            parallel_tool_calls=False,
            tool_choice="none",
            tools=[],
            created_at=10000000,
            model="response-model-id",
            object="response",
        ),
        type="response.created",
        sequence_number=0,
    )

    yield ResponseTextDeltaEvent(
        type="response.output_text.delta",
        item_id="message-id",
        output_index=0,
        content_index=0,
        delta="Hello",
        logprobs=[],
        sequence_number=1,
    )

    yield ResponseTextDeltaEvent(
        type="response.output_text.delta",
        item_id="message-id",
        output_index=0,
        content_index=0,
        delta=" world!",
        logprobs=[],
        sequence_number=2,
    )

    yield ResponseCompletedEvent(
        response=Response(
            id="chat-id",
            output=[
                ResponseOutputMessage(
                    id="message-id",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="Hello world!",
                            type="output_text",
                        ),
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                ),
            ],
            parallel_tool_calls=False,
            tool_choice="none",
            tools=[],
            created_at=10000000,
            model="response-model-id",
            object="response",
            usage=ResponseUsage(
                input_tokens=20,
                input_tokens_details=InputTokensDetails(
                    cached_tokens=5,
                ),
                output_tokens=10,
                output_tokens_details=OutputTokensDetails(
                    reasoning_tokens=8,
                ),
                total_tokens=30,
            ),
        ),
        type="response.completed",
        sequence_number=3,
    )


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
def test_agent_with_instructions():
    def inner(instructions):
        """Create a real Agent instance for testing."""
        return Agent(
            name="test_agent",
            instructions=instructions,
            model="gpt-4",
            model_settings=ModelSettings(
                max_tokens=100,
                temperature=0.7,
                top_p=1.0,
                presence_penalty=0.0,
                frequency_penalty=0.0,
            ),
        )

    return inner


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
async def test_agent_invocation_span_no_pii(
    sentry_init, capture_events, test_agent, mock_model_response
):
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            mock_get_response.return_value = mock_model_response

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=False,
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

    assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in invoke_agent_span["data"]
    assert "gen_ai.request.messages" not in invoke_agent_span["data"]
    assert "gen_ai.response.text" not in invoke_agent_span["data"]

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
@pytest.mark.parametrize(
    "instructions",
    (
        None,
        "You are a coding assistant that talks like a pirate.",
    ),
)
@pytest.mark.parametrize(
    "input",
    [
        pytest.param("Test input", id="string"),
        pytest.param(
            [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="blocks_no_type",
        ),
        pytest.param(
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="blocks",
        ),
        pytest.param(
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="parts_no_type",
        ),
        pytest.param(
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="parts",
        ),
    ],
)
async def test_agent_invocation_span(
    sentry_init,
    capture_events,
    test_agent_with_instructions,
    mock_model_response,
    instructions,
    input,
    request,
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
                test_agent_with_instructions(instructions),
                input,
                run_config=test_run_config,
            )

            assert result is not None
            assert result.final_output == "Hello, how can I help you?"

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span, ai_client_span = spans

    assert transaction["transaction"] == "test_agent workflow"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.openai_agents"

    assert invoke_agent_span["description"] == "invoke_agent test_agent"

    # Only first case checks "gen_ai.request.messages" until further input handling work.
    param_id = request.node.callspec.id
    if "string" in param_id and instructions is None:  # type: ignore
        assert "gen_ai.system_instructions" not in ai_client_span["data"]

        assert invoke_agent_span["data"]["gen_ai.request.messages"] == safe_serialize(
            [
                {"content": [{"text": "Test input", "type": "text"}], "role": "user"},
            ]
        )

    elif "string" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
            ]
        )
    elif "blocks_no_type" in param_id and instructions is None:  # type: ignore
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "blocks_no_type" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "blocks" in param_id and instructions is None:  # type: ignore
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "blocks" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "parts_no_type" in param_id and instructions is None:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ]
        )
    elif "parts_no_type" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ]
        )
    elif instructions is None:  # type: ignore
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ]
        )
    else:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
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


def test_agent_invocation_span_sync_no_pii(
    sentry_init,
    capture_events,
    test_agent,
    mock_model_response,
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
                send_default_pii=False,
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

    assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in invoke_agent_span["data"]


@pytest.mark.parametrize(
    "instructions",
    (
        None,
        "You are a coding assistant that talks like a pirate.",
    ),
)
@pytest.mark.parametrize(
    "input",
    [
        pytest.param("Test input", id="string"),
        pytest.param(
            [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="blocks_no_type",
        ),
        pytest.param(
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="blocks",
        ),
        pytest.param(
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="parts_no_type",
        ),
        pytest.param(
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Test input",
                },
            ],
            id="parts",
        ),
    ],
)
def test_agent_invocation_span_sync(
    sentry_init,
    capture_events,
    test_agent_with_instructions,
    mock_model_response,
    instructions,
    input,
    request,
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

            result = agents.Runner.run_sync(
                test_agent_with_instructions(instructions),
                input,
                run_config=test_run_config,
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

    param_id = request.node.callspec.id
    if "string" in param_id and instructions is None:  # type: ignore
        assert "gen_ai.system_instructions" not in ai_client_span["data"]
    elif "string" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
            ]
        )
    elif "blocks_no_type" in param_id and instructions is None:  # type: ignore
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "blocks_no_type" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "blocks" in param_id and instructions is None:  # type: ignore
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "blocks" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ]
        )
    elif "parts_no_type" in param_id and instructions is None:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ]
        )
    elif "parts_no_type" in param_id:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ]
        )
    elif instructions is None:  # type: ignore
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ]
        )
    else:
        assert ai_client_span["data"]["gen_ai.system_instructions"] == safe_serialize(
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ]
        )


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
async def test_max_turns_before_handoff_span(sentry_init, capture_events, mock_usage):
    """
    Example raising agents.exceptions.AgentsException after the agent invocation span is complete.
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

            with pytest.raises(MaxTurnsExceeded):
                await agents.Runner.run(
                    primary_agent,
                    "Please hand off to secondary agent",
                    run_config=test_run_config,
                    max_turns=1,
                )

    (error, transaction) = events
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
    assert ai_client_span1["data"]["gen_ai.response.tool_calls"] == safe_serialize(
        [
            {
                "arguments": '{"message": "hello"}',
                "call_id": "call_123",
                "name": "simple_test_tool",
                "type": "function_call",
                "id": "call_123",
                "status": None,
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
    assert ai_client_span2["data"]["gen_ai.request.messages"] == safe_serialize(
        [
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
async def test_hosted_mcp_tool_propagation_header_streamed(sentry_init, test_agent):
    """
    Test responses API is given trace propagation headers with HostedMCPTool.
    """

    hosted_tool = HostedMCPTool(
        tool_config={
            "type": "mcp",
            "server_label": "test_server",
            "server_url": "http://example.com/",
            "headers": {
                "baggage": "custom=data",
            },
        },
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(return_value=EXAMPLE_RESPONSE)

    model = OpenAIResponsesModel(model="gpt-4", openai_client=client)

    agent_with_tool = test_agent.clone(
        tools=[hosted_tool],
        model=model,
    )

    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    with patch.object(
        model._client.responses,
        "create",
        side_effect=EXAMPLE_STREAMED_RESPONSE,
    ) as create, mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange", return_value=500000
    ):
        with sentry_sdk.start_transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="01234567890123456789012345678901",
        ) as transaction:
            result = agents.Runner.run_streamed(
                agent_with_tool,
                "Please use the simple test tool",
                run_config=test_run_config,
            )

            async for event in result.stream_events():
                pass

            ai_client_span = transaction._span_recorder.spans[-1]

        args, kwargs = create.call_args

        assert "tools" in kwargs
        assert len(kwargs["tools"]) == 1
        hosted_mcp_tool = kwargs["tools"][0]

        assert hosted_mcp_tool["headers"][
            "sentry-trace"
        ] == "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=ai_client_span.span_id,
            sampled=1,
        )

        expected_outgoing_baggage = (
            "custom=data,"
            "sentry-trace_id=01234567890123456789012345678901,"
            "sentry-sample_rand=0.500000,"
            "sentry-environment=production,"
            "sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,"
            "sentry-transaction=/interactions/other-dogs/new-dog,"
            "sentry-sample_rate=1.0,"
            "sentry-sampled=true"
        )

        assert hosted_mcp_tool["headers"]["baggage"] == expected_outgoing_baggage


@pytest.mark.asyncio
async def test_hosted_mcp_tool_propagation_headers(sentry_init, test_agent):
    """
    Test responses API is given trace propagation headers with HostedMCPTool.
    """

    hosted_tool = HostedMCPTool(
        tool_config={
            "type": "mcp",
            "server_label": "test_server",
            "server_url": "http://example.com/",
            "headers": {
                "baggage": "custom=data",
            },
        },
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(return_value=EXAMPLE_RESPONSE)

    model = OpenAIResponsesModel(model="gpt-4", openai_client=client)

    agent_with_tool = test_agent.clone(
        tools=[hosted_tool],
        model=model,
    )

    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    with patch.object(
        model._client.responses,
        "create",
        wraps=model._client.responses.create,
    ) as create, mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange", return_value=500000
    ):
        with sentry_sdk.start_transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="01234567890123456789012345678901",
        ) as transaction:
            await agents.Runner.run(
                agent_with_tool,
                "Please use the simple test tool",
                run_config=test_run_config,
            )

            ai_client_span = transaction._span_recorder.spans[-1]

        args, kwargs = create.call_args

        assert "tools" in kwargs
        assert len(kwargs["tools"]) == 1
        hosted_mcp_tool = kwargs["tools"][0]

        assert hosted_mcp_tool["headers"][
            "sentry-trace"
        ] == "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=ai_client_span.span_id,
            sampled=1,
        )

        expected_outgoing_baggage = (
            "custom=data,"
            "sentry-trace_id=01234567890123456789012345678901,"
            "sentry-sample_rand=0.500000,"
            "sentry-environment=production,"
            "sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,"
            "sentry-transaction=/interactions/other-dogs/new-dog,"
            "sentry-sample_rate=1.0,"
            "sentry-sampled=true"
        )

        assert hosted_mcp_tool["headers"]["baggage"] == expected_outgoing_baggage


@pytest.mark.asyncio
async def test_model_behavior_error(sentry_init, capture_events, test_agent):
    """
    Example raising agents.exceptions.AgentsException before the agent invocation span is complete.
    The mocked API response indicates that "wrong_tool" was called.
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
                name="wrong_tool",
                type="function_call",
                arguments='{"message": "hello"}',
            )

            tool_response = ModelResponse(
                output=[tool_call],
                usage=Usage(
                    requests=1, input_tokens=10, output_tokens=5, total_tokens=15
                ),
                response_id="resp_tool_123",
            )

            mock_get_response.side_effect = [tool_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            with pytest.raises(ModelBehaviorError):
                await agents.Runner.run(
                    agent_with_tool,
                    "Please use the simple test tool",
                    run_config=test_run_config,
                )

    (error, transaction) = events
    spans = transaction["spans"]
    (
        agent_span,
        ai_client_span1,
    ) = spans

    assert transaction["transaction"] == "test_agent workflow"
    assert transaction["contexts"]["trace"]["origin"] == "auto.ai.openai_agents"

    assert agent_span["description"] == "invoke_agent test_agent"
    assert agent_span["origin"] == "auto.ai.openai_agents"

    # Error due to unrecognized tool in model response.
    assert agent_span["status"] == "internal_error"
    assert agent_span["tags"]["status"] == "internal_error"


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
    assert ai_client_span["status"] == "internal_error"
    assert ai_client_span["tags"]["status"] == "internal_error"


@pytest.mark.asyncio
async def test_error_captures_input_data(sentry_init, capture_events, test_agent):
    """
    Test that input data is captured even when the API call raises an exception.
    This verifies that _set_input_data is called before the API call.
    """
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            mock_get_response.side_effect = Exception("API Error")

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            with pytest.raises(Exception, match="API Error"):
                await agents.Runner.run(
                    test_agent, "Test input", run_config=test_run_config
                )

    (
        error_event,
        transaction,
    ) = events

    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert error_event["exception"]["values"][0]["value"] == "API Error"

    spans = transaction["spans"]
    ai_client_span = [s for s in spans if s["op"] == "gen_ai.chat"][0]

    assert ai_client_span["description"] == "chat gpt-4"
    assert ai_client_span["status"] == "internal_error"
    assert ai_client_span["tags"]["status"] == "internal_error"

    assert "gen_ai.request.messages" in ai_client_span["data"]
    request_messages = safe_serialize(
        [
            {"role": "user", "content": [{"type": "text", "text": "Test input"}]},
        ]
    )
    assert ai_client_span["data"]["gen_ai.request.messages"] == request_messages


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
    assert transaction["spans"][0]["status"] == "internal_error"
    assert transaction["spans"][0]["tags"]["status"] == "internal_error"
    assert transaction["contexts"]["trace"]["status"] == "internal_error"


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
    assert mcp_tool_span.get("status") != "internal_error"
    assert mcp_tool_span.get("tags", {}).get("status") != "internal_error"


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
    assert mcp_tool_span["status"] == "internal_error"
    assert mcp_tool_span["tags"]["status"] == "internal_error"


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


# Test input messages with mixed roles including "ai"
@pytest.mark.parametrize(
    "test_message,expected_role",
    [
        ({"role": "user", "content": "Hello"}, "user"),
        (
            {"role": "ai", "content": "Hi there!"},
            "assistant",
        ),  # Should be mapped to "assistant"
        (
            {"role": "assistant", "content": "How can I help?"},
            "assistant",
        ),  # Should stay "assistant"
    ],
)
def test_openai_agents_message_role_mapping(
    sentry_init, capture_events, test_message, expected_role
):
    """Test that OpenAI Agents integration properly maps message roles like 'ai' to 'assistant'"""
    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    get_response_kwargs = {"input": [test_message]}

    from sentry_sdk.integrations.openai_agents.utils import _set_input_data
    from sentry_sdk import start_span

    with start_span(op="test") as span:
        _set_input_data(span, get_response_kwargs)

    # Verify that messages were processed and roles were mapped
    from sentry_sdk.consts import SPANDATA

    stored_messages = json.loads(span._data[SPANDATA.GEN_AI_REQUEST_MESSAGES])

    # Verify roles were properly mapped
    assert stored_messages[0]["role"] == expected_role


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

    # Create agent with the failing tool
    agent_with_tool = test_agent.clone(tools=[failing_tool])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Create a mock response that includes tool call
            tool_call = ResponseFunctionToolCall(
                id="call_123",
                call_id="call_123",
                name="failing_tool",
                type="function_call",
                arguments='{"message": "test"}',
            )

            # First response with tool call
            tool_response = ModelResponse(
                output=[tool_call],
                usage=Usage(
                    requests=1, input_tokens=10, output_tokens=5, total_tokens=15
                ),
                response_id="resp_tool_123",
            )

            # Second response after tool error (agents library handles the error and continues)
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

            # Note: The agents library catches tool exceptions internally,
            # so we don't expect this to raise
            await agents.Runner.run(
                agent_with_tool,
                "Please use the failing tool",
                run_config=test_run_config,
            )

    (transaction,) = events
    spans = transaction["spans"]

    # Find the execute_tool span
    execute_tool_span = None
    for span in spans:
        if span.get("description", "").startswith("execute_tool failing_tool"):
            execute_tool_span = span
            break

    # Verify the execute_tool span was created
    assert execute_tool_span is not None, "execute_tool span was not created"
    assert execute_tool_span["description"] == "execute_tool failing_tool"
    assert execute_tool_span["data"]["gen_ai.tool.name"] == "failing_tool"

    # Verify error status was set (this is the key test for our patch)
    # The span should be marked as error because the tool execution failed
    assert execute_tool_span["status"] == "internal_error"
    assert execute_tool_span["tags"]["status"] == "internal_error"


@pytest.mark.asyncio
async def test_invoke_agent_span_includes_usage_data(
    sentry_init, capture_events, test_agent, mock_usage
):
    """
    Test that invoke_agent spans include aggregated usage data from context_wrapper.
    This verifies the new functionality added to track token usage in invoke_agent spans.
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # Create a response with usage data
            response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_123",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="Response with usage",
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
            mock_get_response.return_value = response

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
    invoke_agent_span, ai_client_span = spans

    # Verify invoke_agent span has usage data from context_wrapper
    assert invoke_agent_span["description"] == "invoke_agent test_agent"
    assert "gen_ai.usage.input_tokens" in invoke_agent_span["data"]
    assert "gen_ai.usage.output_tokens" in invoke_agent_span["data"]
    assert "gen_ai.usage.total_tokens" in invoke_agent_span["data"]

    # The usage should match the mock_usage values (aggregated across all calls)
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens"] == 10
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens"] == 20
    assert invoke_agent_span["data"]["gen_ai.usage.total_tokens"] == 30
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens.cached"] == 0
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens.reasoning"] == 5


@pytest.mark.asyncio
async def test_ai_client_span_includes_response_model(
    sentry_init, capture_events, test_agent
):
    """
    Test that ai_client spans (gen_ai.chat) include the response model from the actual API response.
    This verifies we capture the actual model used (which may differ from the requested model).
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        # Mock the _fetch_response method to return a response with a model field
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel._fetch_response"
        ) as mock_fetch_response:
            # Create a mock OpenAI Response object with a specific model version
            mock_response = MagicMock()
            mock_response.model = "gpt-4.1-2025-04-14"  # The actual response model
            mock_response.id = "resp_123"
            mock_response.output = [
                ResponseOutputMessage(
                    id="msg_123",
                    type="message",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            text="Hello from GPT-4.1",
                            type="output_text",
                            annotations=[],
                        )
                    ],
                    role="assistant",
                )
            ]
            mock_response.usage = MagicMock()
            mock_response.usage.input_tokens = 10
            mock_response.usage.output_tokens = 20
            mock_response.usage.total_tokens = 30
            mock_response.usage.input_tokens_details = InputTokensDetails(
                cached_tokens=0
            )
            mock_response.usage.output_tokens_details = OutputTokensDetails(
                reasoning_tokens=5
            )

            mock_fetch_response.return_value = mock_response

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
    _, ai_client_span = spans

    # Verify ai_client span has response model from API response
    assert ai_client_span["description"] == "chat gpt-4"
    assert "gen_ai.response.model" in ai_client_span["data"]
    assert ai_client_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"


@pytest.mark.asyncio
async def test_ai_client_span_response_model_with_chat_completions(
    sentry_init, capture_events
):
    """
    Test that response model is captured when using ChatCompletions API (not Responses API).
    This ensures our implementation works with different OpenAI model types.
    """
    # Create agent that uses ChatCompletions model
    agent = Agent(
        name="chat_completions_agent",
        instructions="Test agent using ChatCompletions",
        model="gpt-4o-mini",
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        # Mock the _fetch_response method
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel._fetch_response"
        ) as mock_fetch_response:
            # Create a mock Response object
            mock_response = MagicMock()
            mock_response.model = "gpt-4o-mini-2024-07-18"
            mock_response.id = "resp_123"
            mock_response.output = [
                ResponseOutputMessage(
                    id="msg_123",
                    type="message",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            text="Response from model",
                            type="output_text",
                            annotations=[],
                        )
                    ],
                    role="assistant",
                )
            ]
            mock_response.usage = MagicMock()
            mock_response.usage.input_tokens = 15
            mock_response.usage.output_tokens = 25
            mock_response.usage.total_tokens = 40
            mock_response.usage.input_tokens_details = InputTokensDetails(
                cached_tokens=0
            )
            mock_response.usage.output_tokens_details = OutputTokensDetails(
                reasoning_tokens=0
            )

            mock_fetch_response.return_value = mock_response

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            result = await agents.Runner.run(
                agent, "Test input", run_config=test_run_config
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    _, ai_client_span = spans

    # Verify response model from API response is captured
    assert "gen_ai.response.model" in ai_client_span["data"]
    assert ai_client_span["data"]["gen_ai.response.model"] == "gpt-4o-mini-2024-07-18"


@pytest.mark.asyncio
async def test_multiple_llm_calls_aggregate_usage(
    sentry_init, capture_events, test_agent
):
    """
    Test that invoke_agent spans show aggregated usage across multiple LLM calls
    (e.g., when tools are used and multiple API calls are made).
    """

    @agents.function_tool
    def calculator(a: int, b: int) -> int:
        """Add two numbers"""
        return a + b

    agent_with_tool = test_agent.clone(tools=[calculator])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            # First call: agent decides to use tool (10 input, 5 output tokens)
            tool_call_response = ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        id="call_123",
                        call_id="call_123",
                        name="calculator",
                        type="function_call",
                        arguments='{"a": 5, "b": 3}',
                    )
                ],
                usage=Usage(
                    requests=1,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    input_tokens_details=InputTokensDetails(cached_tokens=0),
                    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
                ),
                response_id="resp_tool_call",
            )

            # Second call: agent uses tool result to respond (20 input, 15 output tokens)
            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="The result is 8",
                                type="output_text",
                                annotations=[],
                            )
                        ],
                        role="assistant",
                    )
                ],
                usage=Usage(
                    requests=1,
                    input_tokens=20,
                    output_tokens=15,
                    total_tokens=35,
                    input_tokens_details=InputTokensDetails(cached_tokens=5),
                    output_tokens_details=OutputTokensDetails(reasoning_tokens=3),
                ),
                response_id="resp_final",
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
                "What is 5 + 3?",
                run_config=test_run_config,
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span = spans[0]

    # Verify invoke_agent span has aggregated usage from both API calls
    # Total: 10 + 20 = 30 input tokens, 5 + 15 = 20 output tokens, 15 + 35 = 50 total
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens"] == 30
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens"] == 20
    assert invoke_agent_span["data"]["gen_ai.usage.total_tokens"] == 50
    # Cached tokens should be aggregated: 0 + 5 = 5
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens.cached"] == 5
    # Reasoning tokens should be aggregated: 0 + 3 = 3
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens.reasoning"] == 3


@pytest.mark.asyncio
async def test_response_model_not_set_when_unavailable(
    sentry_init, capture_events, test_agent
):
    """
    Test that response model is not set if the API response doesn't have a model field.
    The request model should still be set correctly.
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel._fetch_response"
        ) as mock_fetch_response:
            # Create a mock response without a model field
            mock_response = MagicMock()
            mock_response.model = None  # No model in response
            mock_response.id = "resp_123"
            mock_response.output = [
                ResponseOutputMessage(
                    id="msg_123",
                    type="message",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            text="Response without model field",
                            type="output_text",
                            annotations=[],
                        )
                    ],
                    role="assistant",
                )
            ]
            mock_response.usage = MagicMock()
            mock_response.usage.input_tokens = 10
            mock_response.usage.output_tokens = 20
            mock_response.usage.total_tokens = 30
            mock_response.usage.input_tokens_details = InputTokensDetails(
                cached_tokens=0
            )
            mock_response.usage.output_tokens_details = OutputTokensDetails(
                reasoning_tokens=0
            )

            mock_fetch_response.return_value = mock_response

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            result = await agents.Runner.run(
                test_agent, "Test input", run_config=test_run_config
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    _, ai_client_span = spans

    # Response model should NOT be set when API doesn't return it
    assert "gen_ai.response.model" not in ai_client_span["data"]
    # But request model should still be set
    assert "gen_ai.request.model" in ai_client_span["data"]
    assert ai_client_span["data"]["gen_ai.request.model"] == "gpt-4"


@pytest.mark.asyncio
async def test_invoke_agent_span_includes_response_model(
    sentry_init, capture_events, test_agent
):
    """
    Test that invoke_agent spans include the response model from the API response.
    """

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel._fetch_response"
        ) as mock_fetch_response:
            # Create a mock OpenAI Response object with a specific model version
            mock_response = MagicMock()
            mock_response.model = "gpt-4.1-2025-04-14"  # The actual response model
            mock_response.id = "resp_123"
            mock_response.output = [
                ResponseOutputMessage(
                    id="msg_123",
                    type="message",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            text="Response from model",
                            type="output_text",
                            annotations=[],
                        )
                    ],
                    role="assistant",
                )
            ]
            mock_response.usage = MagicMock()
            mock_response.usage.input_tokens = 10
            mock_response.usage.output_tokens = 20
            mock_response.usage.total_tokens = 30
            mock_response.usage.input_tokens_details = InputTokensDetails(
                cached_tokens=0
            )
            mock_response.usage.output_tokens_details = OutputTokensDetails(
                reasoning_tokens=5
            )

            mock_fetch_response.return_value = mock_response

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
    invoke_agent_span, ai_client_span = spans

    # Verify invoke_agent span has response model from API
    assert invoke_agent_span["description"] == "invoke_agent test_agent"
    assert "gen_ai.response.model" in invoke_agent_span["data"]
    assert invoke_agent_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"

    # Also verify ai_client span has it
    assert "gen_ai.response.model" in ai_client_span["data"]
    assert ai_client_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"


@pytest.mark.asyncio
async def test_invoke_agent_span_uses_last_response_model(
    sentry_init, capture_events, test_agent
):
    """
    Test that when an agent makes multiple LLM calls (e.g., with tools),
    the invoke_agent span reports the last response model used.
    """

    @agents.function_tool
    def calculator(a: int, b: int) -> int:
        """Add two numbers"""
        return a + b

    agent_with_tool = test_agent.clone(tools=[calculator])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel._fetch_response"
        ) as mock_fetch_response:
            # First call: gpt-4 model returns tool call
            first_response = MagicMock()
            first_response.model = "gpt-4-0613"
            first_response.id = "resp_1"
            first_response.output = [
                ResponseFunctionToolCall(
                    id="call_123",
                    call_id="call_123",
                    name="calculator",
                    type="function_call",
                    arguments='{"a": 5, "b": 3}',
                )
            ]
            first_response.usage = MagicMock()
            first_response.usage.input_tokens = 10
            first_response.usage.output_tokens = 5
            first_response.usage.total_tokens = 15
            first_response.usage.input_tokens_details = InputTokensDetails(
                cached_tokens=0
            )
            first_response.usage.output_tokens_details = OutputTokensDetails(
                reasoning_tokens=0
            )

            # Second call: different model version returns final message
            second_response = MagicMock()
            second_response.model = "gpt-4.1-2025-04-14"
            second_response.id = "resp_2"
            second_response.output = [
                ResponseOutputMessage(
                    id="msg_final",
                    type="message",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            text="The result is 8",
                            type="output_text",
                            annotations=[],
                        )
                    ],
                    role="assistant",
                )
            ]
            second_response.usage = MagicMock()
            second_response.usage.input_tokens = 20
            second_response.usage.output_tokens = 15
            second_response.usage.total_tokens = 35
            second_response.usage.input_tokens_details = InputTokensDetails(
                cached_tokens=5
            )
            second_response.usage.output_tokens_details = OutputTokensDetails(
                reasoning_tokens=3
            )

            mock_fetch_response.side_effect = [first_response, second_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
                send_default_pii=True,
            )

            events = capture_events()

            result = await agents.Runner.run(
                agent_with_tool,
                "What is 5 + 3?",
                run_config=test_run_config,
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span = spans[0]
    first_ai_client_span = spans[1]
    second_ai_client_span = spans[3]  # After tool span

    # Invoke_agent span uses the LAST response model
    assert "gen_ai.response.model" in invoke_agent_span["data"]
    assert invoke_agent_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"

    # Each ai_client span has its own response model from the API
    assert first_ai_client_span["data"]["gen_ai.response.model"] == "gpt-4-0613"
    assert (
        second_ai_client_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"
    )


def test_openai_agents_message_truncation(sentry_init, capture_events):
    """Test that large messages are truncated properly in OpenAI Agents integration."""

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )

    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    test_messages = [
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": large_content},
        {"role": "user", "content": "small message 4"},
        {"role": "assistant", "content": "small message 5"},
    ]

    get_response_kwargs = {"input": test_messages}

    with start_span(op="gen_ai.chat") as span:
        scope = sentry_sdk.get_current_scope()
        _set_input_data(span, get_response_kwargs)
        if hasattr(scope, "_gen_ai_original_message_count"):
            truncated_count = scope._gen_ai_original_message_count.get(span.span_id)
            assert truncated_count == 4, (
                f"Expected 4 original messages, got {truncated_count}"
            )

        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span._data
        messages_data = span._data[SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert isinstance(messages_data, str)

        parsed_messages = json.loads(messages_data)
        assert isinstance(parsed_messages, list)
        assert len(parsed_messages) == 1
        assert "small message 5" in str(parsed_messages[0])


@pytest.mark.asyncio
async def test_streaming_span_update_captures_response_data(
    sentry_init, test_agent, mock_usage
):
    """
    Test that update_ai_client_span correctly captures response text,
    usage data, and response model from a streaming response.
    """
    from sentry_sdk.integrations.openai_agents.spans.ai_client import (
        update_ai_client_span,
    )

    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    # Create a mock streaming response object (similar to what we'd get from ResponseCompletedEvent)
    mock_streaming_response = MagicMock()
    mock_streaming_response.model = "gpt-4-streaming"
    mock_streaming_response.usage = mock_usage
    mock_streaming_response.output = [
        ResponseOutputMessage(
            id="msg_streaming_123",
            type="message",
            status="completed",
            content=[
                ResponseOutputText(
                    text="Hello from streaming!",
                    type="output_text",
                    annotations=[],
                )
            ],
            role="assistant",
        )
    ]

    # Test the unified update function (works for both streaming and non-streaming)
    with start_span(op="gen_ai.chat", description="test chat") as span:
        update_ai_client_span(span, mock_streaming_response)

        # Verify the span data was set correctly
        assert span._data["gen_ai.response.text"] == "Hello from streaming!"
        assert span._data["gen_ai.usage.input_tokens"] == 10
        assert span._data["gen_ai.usage.output_tokens"] == 20
        assert span._data["gen_ai.response.model"] == "gpt-4-streaming"


@pytest.mark.asyncio
async def test_streaming_ttft_on_chat_span(sentry_init, test_agent):
    """
    Test that time-to-first-token (TTFT) is recorded on chat spans during streaming.

    TTFT is triggered by events with a `delta` attribute, which includes:
    - ResponseTextDeltaEvent (text output)
    - ResponseAudioDeltaEvent (audio output)
    - ResponseReasoningTextDeltaEvent (reasoning/thinking)
    - ResponseFunctionCallArgumentsDeltaEvent (function call args)
    - and other delta events...

    Events WITHOUT delta (like ResponseCompletedEvent, ResponseCreatedEvent, etc.)
    should NOT trigger TTFT.
    """
    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(return_value=EXAMPLE_RESPONSE)

    model = OpenAIResponsesModel(model="gpt-4", openai_client=client)

    agent_with_tool = test_agent.clone(
        model=model,
    )

    sentry_init(
        integrations=[OpenAIAgentsIntegration()],
        traces_sample_rate=1.0,
    )

    with patch.object(
        model._client.responses,
        "create",
        side_effect=EXAMPLE_STREAMED_RESPONSE_WITH_DELTA,
    ) as _:
        with sentry_sdk.start_transaction(
            name="test_ttft", sampled=True
        ) as transaction:
            result = agents.Runner.run_streamed(
                agent_with_tool,
                "Please use the simple test tool",
                run_config=test_run_config,
            )

            async for event in result.stream_events():
                pass

            # Verify TTFT is recorded on the chat span (must be inside transaction context)
            chat_spans = [
                s for s in transaction._span_recorder.spans if s.op == "gen_ai.chat"
            ]
            assert len(chat_spans) >= 1
            chat_span = chat_spans[0]

            assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in chat_span._data
            assert chat_span._data.get(SPANDATA.GEN_AI_RESPONSE_STREAMING) is True


@pytest.mark.skipif(
    parse_version(OPENAI_AGENTS_VERSION) < (0, 4, 0),
    reason="conversation_id support requires openai-agents >= 0.4.0",
)
@pytest.mark.asyncio
async def test_conversation_id_on_all_spans(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """
    Test that gen_ai.conversation.id is set on all AI-related spans when passed to Runner.run().
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
                test_agent,
                "Test input",
                run_config=test_run_config,
                conversation_id="conv_test_123",
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span, ai_client_span = spans

    # Verify workflow span (transaction) has conversation_id
    assert (
        transaction["contexts"]["trace"]["data"]["gen_ai.conversation.id"]
        == "conv_test_123"
    )

    # Verify invoke_agent span has conversation_id
    assert invoke_agent_span["data"]["gen_ai.conversation.id"] == "conv_test_123"

    # Verify ai_client span has conversation_id
    assert ai_client_span["data"]["gen_ai.conversation.id"] == "conv_test_123"


@pytest.mark.skipif(
    parse_version(OPENAI_AGENTS_VERSION) < (0, 4, 0),
    reason="conversation_id support requires openai-agents >= 0.4.0",
)
@pytest.mark.asyncio
async def test_conversation_id_on_tool_span(sentry_init, capture_events, test_agent):
    """
    Test that gen_ai.conversation.id is set on tool execution spans when passed to Runner.run().
    """

    @agents.function_tool
    def simple_tool(message: str) -> str:
        """A simple tool"""
        return f"Result: {message}"

    agent_with_tool = test_agent.clone(tools=[simple_tool])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch(
            "agents.models.openai_responses.OpenAIResponsesModel.get_response"
        ) as mock_get_response:
            tool_call = ResponseFunctionToolCall(
                id="call_123",
                call_id="call_123",
                name="simple_tool",
                type="function_call",
                arguments='{"message": "hello"}',
            )

            tool_response = ModelResponse(
                output=[tool_call],
                usage=Usage(
                    requests=1, input_tokens=10, output_tokens=5, total_tokens=15
                ),
                response_id="resp_tool_456",
            )

            final_response = ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="msg_final",
                        type="message",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                text="Done",
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
                response_id="resp_final_789",
            )

            mock_get_response.side_effect = [tool_response, final_response]

            sentry_init(
                integrations=[OpenAIAgentsIntegration()],
                traces_sample_rate=1.0,
            )

            events = capture_events()

            await agents.Runner.run(
                agent_with_tool,
                "Use the tool",
                run_config=test_run_config,
                conversation_id="conv_tool_test_456",
            )

    (transaction,) = events
    spans = transaction["spans"]

    # Find the tool span
    tool_span = None
    for span in spans:
        if span.get("description", "").startswith("execute_tool"):
            tool_span = span
            break

    assert tool_span is not None
    # Tool span should have the conversation_id passed to Runner.run()
    assert tool_span["data"]["gen_ai.conversation.id"] == "conv_tool_test_456"

    # Workflow span (transaction) should have the same conversation_id
    assert (
        transaction["contexts"]["trace"]["data"]["gen_ai.conversation.id"]
        == "conv_tool_test_456"
    )


@pytest.mark.skipif(
    parse_version(OPENAI_AGENTS_VERSION) < (0, 4, 0),
    reason="conversation_id support requires openai-agents >= 0.4.0",
)
@pytest.mark.asyncio
async def test_no_conversation_id_when_not_provided(
    sentry_init, capture_events, test_agent, mock_model_response
):
    """
    Test that gen_ai.conversation.id is not set when not passed to Runner.run().
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

            # Don't pass conversation_id
            result = await agents.Runner.run(
                test_agent, "Test input", run_config=test_run_config
            )

            assert result is not None

    (transaction,) = events
    spans = transaction["spans"]
    invoke_agent_span, ai_client_span = spans

    # Verify conversation_id is NOT set on any spans
    assert "gen_ai.conversation.id" not in transaction["contexts"]["trace"].get(
        "data", {}
    )
    assert "gen_ai.conversation.id" not in invoke_agent_span.get("data", {})
    assert "gen_ai.conversation.id" not in ai_client_span.get("data", {})

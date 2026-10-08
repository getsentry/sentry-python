import json

import pytest

import sentry_sdk
from sentry_sdk.utils import package_version

try:
    from openai import NOT_GIVEN
except ImportError:
    NOT_GIVEN = None
try:
    from openai import Omit, omit
except ImportError:
    omit = None
    Omit = None

from openai import AsyncOpenAI, AsyncStream, OpenAI, OpenAIError, Stream
from openai.types import CompletionUsage, CreateEmbeddingResponse, Embedding
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessage,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as DeltaChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.create_embedding_response import Usage as EmbeddingTokenUsage

try:
    from openai.types.completion_usage import (
        CompletionTokensDetails,
        PromptTokensDetails,
    )
except ImportError:
    CompletionTokensDetails = None
    PromptTokensDetails = None

try:
    from openai.types.chat import (
        ChatCompletionCustomToolParam,
        ChatCompletionFunctionToolParam,
    )
    from openai.types.chat.chat_completion_custom_tool_param import Custom
    from openai.types.shared_params import FunctionDefinition
except ImportError:
    pass

SKIP_RESPONSES_TESTS = False

try:
    from openai.types.responses import (
        CustomToolParam,
        FunctionToolParam,
        Response,
        ResponseOutputMessage,
        ResponseOutputText,
        ResponseUsage,
        WebSearchToolParam,
    )
    from openai.types.responses.response_completed_event import ResponseCompletedEvent
    from openai.types.responses.response_created_event import ResponseCreatedEvent
    from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
    from openai.types.responses.response_usage import (
        InputTokensDetails,
        OutputTokensDetails,
    )
except ImportError:
    SKIP_RESPONSES_TESTS = True

from unittest import mock  # python 3.3 and above

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.openai import OpenAIIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.utils import safe_serialize

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


OPENAI_VERSION = package_version("openai")


if SKIP_RESPONSES_TESTS:
    EXAMPLE_RESPONSE = None
else:
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
                cache_write_tokens=0,
            ),
            output_tokens=10,
            output_tokens_details=OutputTokensDetails(
                reasoning_tokens=8,
            ),
            total_tokens=30,
        ),
    )

EXAMPLE_TOOLS = [
    {
        "type": "function",
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city and state, e.g. San Francisco, CA",
                },
            },
            "required": ["location"],
        },
    }
]

EXAMPLE_COMPLETIONS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA",
                    },
                },
                "required": ["location"],
            },
        },
    }
]


@pytest.mark.skipif(
    OPENAI_VERSION <= (2, 10, 0),
    reason="ChatCompletionCustomToolParam is unavailable before.",
)
def test_chat_completion_tool_definitions(
    sentry_init,
    capture_items,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("span")

    client.chat.completions.create(
        model="some-model",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hello"},
        ],
        tools=[
            ChatCompletionFunctionToolParam(
                type="function",
                function=FunctionDefinition(
                    name="name",
                    description="description",
                    parameters={
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                            "state": {"type": "string"},
                        },
                        "required": ["city", "state"],
                        "additionalProperties": False,
                    },
                    strict=True,
                ),
            ),
            ChatCompletionCustomToolParam(
                type="custom",
                custom=Custom(
                    name="name",
                    description="description",
                ),
            ),
        ],
    )

    sentry_sdk.flush()
    span = next(item.payload for item in items)

    assert json.loads(span["attributes"][SPANDATA.GEN_AI_TOOL_DEFINITIONS]) == [
        {
            "type": "function",
            "name": "name",
            "description": "description",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "state": {"type": "string"},
                },
                "required": ["city", "state"],
                "additionalProperties": False,
            },
        },
        {
            "type": "custom",
            "name": "name",
            "description": "description",
        },
    ]


def test_nonstreaming_chat_completion_no_sensitive_data(
    sentry_init,
    capture_items,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("span")

    response = (
        client.chat.completions.create(
            model="some-model",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "hello"},
            ],
            max_tokens=100,
            presence_penalty=0.1,
            frequency_penalty=0.2,
            temperature=0.7,
            top_p=0.9,
        )
        .choices[0]
        .message.content
    )

    assert response == "the model response"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize(
    "get_messages,expected_system_instructions",
    [
        (
            lambda: [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ],
        ),
        (
            lambda: [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
        ),
        (
            lambda: iter(
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
                        "content": "Message demonstrating the absence of truncation.",
                    },
                    {"role": "user", "content": "hello"},
                ]
            ),
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
        ),
    ],
)
def test_nonstreaming_chat_completion(
    sentry_init,
    capture_items,
    get_messages,
    expected_system_instructions,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("span")

    response = (
        client.chat.completions.create(
            model="some-model",
            messages=get_messages(),
            max_tokens=100,
            presence_penalty=0.1,
            frequency_penalty=0.2,
            temperature=0.7,
            top_p=0.9,
        )
        .choices[0]
        .message.content
    )

    assert response == "the model response"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
        == expected_system_instructions
    )

    assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert (
        "Message demonstrating the absence of truncation."
        in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    )
    assert "the model response" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
async def test_nonstreaming_chat_completion_async_no_sensitive_data(
    sentry_init,
    capture_items,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = mock.AsyncMock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("span")

    response = await client.chat.completions.create(
        model="some-model",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hello"},
        ],
        max_tokens=100,
        presence_penalty=0.1,
        frequency_penalty=0.2,
        temperature=0.7,
        top_p=0.9,
    )
    response = response.choices[0].message.content

    assert response == "the model response"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_messages,expected_system_instructions",
    [
        (
            lambda: [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ],
        ),
        (
            lambda: [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
        ),
        (
            lambda: iter(
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
                        "content": "Message demonstrating the absence of truncation.",
                    },
                    {"role": "user", "content": "hello"},
                ]
            ),
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
        ),
    ],
)
async def test_nonstreaming_chat_completion_async(
    sentry_init,
    capture_items,
    get_messages,
    expected_system_instructions,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = AsyncMock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("span")

    response = await client.chat.completions.create(
        model="some-model",
        messages=get_messages(),
        max_tokens=100,
        presence_penalty=0.1,
        frequency_penalty=0.2,
        temperature=0.7,
        top_p=0.9,
    )
    response = response.choices[0].message.content

    assert response == "the model response"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
        == expected_system_instructions
    )

    assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert (
        "Message demonstrating the absence of truncation."
        in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    )
    assert "the model response" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


def tiktoken_encoding_if_installed():
    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        return "cl100k_base"
    except ImportError:
        return None


# noinspection PyTypeChecker
def test_streaming_chat_completion_no_sensitive_data(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            [
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=0,
                            delta=ChoiceDelta(content="hel"),
                            finish_reason=None,
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=1,
                            delta=ChoiceDelta(content="lo "),
                            finish_reason=None,
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=2,
                            delta=ChoiceDelta(content="world"),
                            finish_reason="stop",
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
            ],
            include_event_type=False,
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.chat.completions.create(
            model="some-model",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "hello"},
            ],
            stream=True,
            max_tokens=100,
            presence_penalty=0.1,
            frequency_penalty=0.2,
            temperature=0.7,
            top_p=0.9,
        )
        response_string = "".join(
            map(lambda x: x.choices[0].delta.content, response_stream)
        )

    assert response_string == "hello world"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

    assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        assert span["attributes"]["gen_ai.usage.output_tokens"] == 2
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 7
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 9
    except ImportError:
        pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
def test_streaming_chat_completion_with_usage_in_stream(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """When stream_options=include_usage is set, token usage comes from the final chunk's usage field."""
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            [
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=0,
                            delta=ChoiceDelta(content="hel"),
                            finish_reason=None,
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=0,
                            delta=ChoiceDelta(content="lo"),
                            finish_reason="stop",
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                    usage=CompletionUsage(
                        prompt_tokens=20,
                        completion_tokens=10,
                        total_tokens=30,
                    ),
                ),
            ],
            include_event_type=False,
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
def test_streaming_chat_completion_empty_content_preserves_token_usage(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """Token usage from the stream is recorded even when no content is produced (e.g. content filter)."""
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            [
                ChatCompletionChunk(
                    id="1",
                    choices=[],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                    usage=CompletionUsage(
                        prompt_tokens=20,
                        completion_tokens=0,
                        total_tokens=20,
                    ),
                ),
            ],
            include_event_type=False,
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert "gen_ai.usage.output_tokens" not in span["attributes"]
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 20


@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
@pytest.mark.asyncio
async def test_streaming_chat_completion_empty_content_preserves_token_usage_async(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    """Token usage from the stream is recorded even when no content is produced - async variant."""
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(
            server_side_event_chunks(
                [
                    ChatCompletionChunk(
                        id="1",
                        choices=[],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                        usage=CompletionUsage(
                            prompt_tokens=20,
                            completion_tokens=0,
                            total_tokens=20,
                        ),
                    ),
                ],
                include_event_type=False,
            )
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        async for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert "gen_ai.usage.output_tokens" not in span["attributes"]
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 20


@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
@pytest.mark.asyncio
async def test_streaming_chat_completion_async_with_usage_in_stream(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    """When stream_options=include_usage is set, token usage comes from the final chunk's usage field (async)."""
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(
            server_side_event_chunks(
                [
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=0,
                                delta=ChoiceDelta(content="hel"),
                                finish_reason=None,
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=0,
                                delta=ChoiceDelta(content="lo"),
                                finish_reason="stop",
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                        usage=CompletionUsage(
                            prompt_tokens=20,
                            completion_tokens=10,
                            total_tokens=30,
                        ),
                    ),
                ],
                include_event_type=False,
            )
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        async for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


# noinspection PyTypeChecker
@pytest.mark.parametrize(
    "get_messages,expected_system_instructions,expected_output_tokens,expected_input_tokens",
    [
        (
            lambda: [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ],
            2,
            15,
        ),
        (
            lambda: [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
            2,
            20,
        ),
        (
            lambda: iter(
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
                        "content": "Message demonstrating the absence of truncation.",
                    },
                    {"role": "user", "content": "hello"},
                ]
            ),
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
            2,
            20,
        ),
    ],
)
def test_streaming_chat_completion(
    sentry_init,
    capture_items,
    get_messages,
    expected_system_instructions,
    expected_output_tokens,
    expected_input_tokens,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            [
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=0,
                            delta=ChoiceDelta(content="hel"),
                            finish_reason=None,
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=1,
                            delta=ChoiceDelta(content="lo "),
                            finish_reason=None,
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=2,
                            delta=ChoiceDelta(content="world"),
                            finish_reason="stop",
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
            ],
            include_event_type=False,
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.chat.completions.create(
            model="some-model",
            messages=get_messages(),
            stream=True,
            max_tokens=100,
            presence_penalty=0.1,
            frequency_penalty=0.2,
            temperature=0.7,
            top_p=0.9,
        )
        response_string = "".join(
            map(lambda x: x.choices[0].delta.content, response_stream)
        )
    assert response_string == "hello world"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
        == expected_system_instructions
    )

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

    assert (
        "Message demonstrating the absence of truncation."
        in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    )
    assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert "hello world" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        assert (
            span["attributes"]["gen_ai.usage.output_tokens"] == expected_output_tokens
        )
        assert span["attributes"]["gen_ai.usage.input_tokens"] == expected_input_tokens
        assert (
            span["attributes"]["gen_ai.usage.total_tokens"]
            == expected_output_tokens + expected_input_tokens
        )

    except ImportError:
        pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


# noinspection PyTypeChecker
@pytest.mark.asyncio
async def test_streaming_chat_completion_async_no_sensitive_data(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(
            server_side_event_chunks(
                [
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=0,
                                delta=ChoiceDelta(content="hel"),
                                finish_reason=None,
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=1,
                                delta=ChoiceDelta(content="lo "),
                                finish_reason=None,
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=2,
                                delta=ChoiceDelta(content="world"),
                                finish_reason="stop",
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                ],
                include_event_type=False,
            )
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.chat.completions.create(
            model="some-model",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "hello"},
            ],
            stream=True,
            max_tokens=100,
            presence_penalty=0.1,
            frequency_penalty=0.2,
            temperature=0.7,
            top_p=0.9,
        )

        response_string = ""
        async for x in response_stream:
            response_string += x.choices[0].delta.content

    assert response_string == "hello world"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

    assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        assert span["attributes"]["gen_ai.usage.output_tokens"] == 2
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 7
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 9

    except ImportError:
        pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


# noinspection PyTypeChecker
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_messages,expected_system_instructions,expected_output_tokens,expected_input_tokens",
    [
        (
            lambda: [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ],
            2,
            15,
        ),
        (
            lambda: [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
            2,
            20,
        ),
        (
            lambda: iter(
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
                        "content": "Message demonstrating the absence of truncation.",
                    },
                    {"role": "user", "content": "hello"},
                ]
            ),
            [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ],
            2,
            20,
        ),
    ],
)
async def test_streaming_chat_completion_async(
    sentry_init,
    capture_items,
    get_messages,
    expected_system_instructions,
    expected_output_tokens,
    expected_input_tokens,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")

    returned_stream = get_model_response(
        async_iterator(
            server_side_event_chunks(
                [
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=0,
                                delta=ChoiceDelta(content="hel"),
                                finish_reason=None,
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=1,
                                delta=ChoiceDelta(content="lo "),
                                finish_reason=None,
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=2,
                                delta=ChoiceDelta(content="world"),
                                finish_reason="stop",
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                ],
                include_event_type=False,
            )
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.chat.completions.create(
            model="some-model",
            messages=get_messages(),
            stream=True,
            max_tokens=100,
            presence_penalty=0.1,
            frequency_penalty=0.2,
            temperature=0.7,
            top_p=0.9,
        )

        response_string = ""
        async for x in response_stream:
            response_string += x.choices[0].delta.content

    assert response_string == "hello world"
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
        == expected_system_instructions
    )

    assert (
        "Message demonstrating the absence of truncation."
        in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    )
    assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert "hello world" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        assert (
            span["attributes"]["gen_ai.usage.output_tokens"] == expected_output_tokens
        )
        assert span["attributes"]["gen_ai.usage.input_tokens"] == expected_input_tokens
        assert (
            span["attributes"]["gen_ai.usage.total_tokens"]
            == expected_output_tokens + expected_input_tokens
        )

    except ImportError:
        pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


def test_bad_chat_completion(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )
    items = capture_items("event", "span")

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )
    with pytest.raises(OpenAIError):
        client.chat.completions.create(
            model="some-model",
            messages=[{"role": "system", "content": "hello"}],
        )

    (event,) = (item.payload for item in items if item.type == "event")
    sentry_sdk.flush()
    (span,) = (item.payload for item in items if item.type == "span")
    assert event["level"] == "error"
    assert span["status"] == "error"


def test_span_status_error(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )
    items = capture_items("event", "span")

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )
    with pytest.raises(OpenAIError):
        client.chat.completions.create(
            model="some-model",
            messages=[{"role": "system", "content": "hello"}],
        )

    (error,) = (item.payload for item in items if item.type == "event")
    assert error["level"] == "error"

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["status"] == "error"


@pytest.mark.asyncio
async def test_bad_chat_completion_async(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )
    items = capture_items("event", "span")

    with pytest.raises(OpenAIError):
        await client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

    (event,) = (item.payload for item in items if item.type == "event")
    sentry_sdk.flush()
    (span,) = (item.payload for item in items if item.type == "span")
    assert event["level"] == "error"
    assert span["status"] == "error"


def test_embeddings_create_no_sensitive_data(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = OpenAI(api_key="z")

    returned_embedding = CreateEmbeddingResponse(
        data=[Embedding(object="embedding", index=0, embedding=[1.0, 2.0, 3.0])],
        model="some-model",
        object="list",
        usage=EmbeddingTokenUsage(
            prompt_tokens=20,
            total_tokens=30,
        ),
    )

    client.embeddings._post = mock.Mock(return_value=returned_embedding)
    items = capture_items("span")

    response = client.embeddings.create(input="hello", model="text-embedding-3-large")

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize(
    "get_input,expected_embeddings_input",
    [
        (
            lambda: "hello",
            ["hello"],
        ),
        (
            lambda: ["First text", "Second text", "Third text"],
            [
                "First text",
                "Second text",
                "Third text",
            ],
        ),
        (
            lambda: iter(["First text", "Second text", "Third text"]),
            [
                "First text",
                "Second text",
                "Third text",
            ],
        ),
        (
            lambda: [5, 8, 13, 21, 34],
            [
                5,
                8,
                13,
                21,
                34,
            ],
        ),
        (
            lambda: iter(
                [5, 8, 13, 21, 34],
            ),
            [
                5,
                8,
                13,
                21,
                34,
            ],
        ),
        (
            lambda: [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
            [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
        ),
        (
            lambda: iter(
                [
                    [5, 8, 13, 21, 34],
                    [8, 13, 21, 34, 55],
                ]
            ),
            [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
        ),
    ],
)
def test_embeddings_create(
    sentry_init,
    capture_items,
    get_input,
    expected_embeddings_input,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")

    returned_embedding = CreateEmbeddingResponse(
        data=[Embedding(object="embedding", index=0, embedding=[1.0, 2.0, 3.0])],
        model="some-model",
        object="list",
        usage=EmbeddingTokenUsage(
            prompt_tokens=20,
            total_tokens=30,
        ),
    )

    client.embeddings._post = mock.Mock(return_value=returned_embedding)
    items = capture_items("span")

    response = client.embeddings.create(
        input=get_input(), model="text-embedding-3-large"
    )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
        == expected_embeddings_input
    )

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
async def test_embeddings_create_async_no_sensitive_data(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")

    returned_embedding = CreateEmbeddingResponse(
        data=[Embedding(object="embedding", index=0, embedding=[1.0, 2.0, 3.0])],
        model="some-model",
        object="list",
        usage=EmbeddingTokenUsage(
            prompt_tokens=20,
            total_tokens=30,
        ),
    )

    client.embeddings._post = AsyncMock(return_value=returned_embedding)
    items = capture_items("span")

    response = await client.embeddings.create(
        input="hello", model="text-embedding-3-large"
    )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_input,expected_embeddings_input",
    [
        (
            lambda: "hello",
            ["hello"],
        ),
        (
            lambda: ["First text", "Second text", "Third text"],
            [
                "First text",
                "Second text",
                "Third text",
            ],
        ),
        (
            lambda: iter(["First text", "Second text", "Third text"]),
            [
                "First text",
                "Second text",
                "Third text",
            ],
        ),
        (
            lambda: [5, 8, 13, 21, 34],
            [
                5,
                8,
                13,
                21,
                34,
            ],
        ),
        (
            lambda: iter(
                [5, 8, 13, 21, 34],
            ),
            [
                5,
                8,
                13,
                21,
                34,
            ],
        ),
        (
            lambda: [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
            [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
        ),
        (
            lambda: iter(
                [
                    [5, 8, 13, 21, 34],
                    [8, 13, 21, 34, 55],
                ]
            ),
            [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
        ),
    ],
)
async def test_embeddings_create_async(
    sentry_init,
    capture_items,
    get_input,
    expected_embeddings_input,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")

    returned_embedding = CreateEmbeddingResponse(
        data=[Embedding(object="embedding", index=0, embedding=[1.0, 2.0, 3.0])],
        model="some-model",
        object="list",
        usage=EmbeddingTokenUsage(
            prompt_tokens=20,
            total_tokens=30,
        ),
    )

    client.embeddings._post = AsyncMock(return_value=returned_embedding)
    items = capture_items("span")

    response = await client.embeddings.create(
        input=get_input(), model="text-embedding-3-large"
    )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
        == expected_embeddings_input
    )

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


def test_embeddings_create_raises_error(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")

    client.embeddings._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )
    items = capture_items("event", "span")

    with pytest.raises(OpenAIError):
        client.embeddings.create(input="hello", model="text-embedding-3-large")

    (event,) = (item.payload for item in items if item.type == "event")
    sentry_sdk.flush()
    (span,) = (item.payload for item in items if item.type == "span")
    assert event["level"] == "error"
    assert span["status"] == "error"


@pytest.mark.asyncio
async def test_embeddings_create_raises_error_async(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = AsyncOpenAI(api_key="z")

    client.embeddings._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )
    items = capture_items("event", "span")

    with pytest.raises(OpenAIError):
        await client.embeddings.create(input="hello", model="text-embedding-3-large")

    (event,) = (item.payload for item in items if item.type == "event")
    sentry_sdk.flush()
    (span,) = (item.payload for item in items if item.type == "span")
    assert event["level"] == "error"
    assert span["status"] == "error"


def test_span_origin_nonstreaming_chat(
    sentry_init,
    capture_items,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("transaction", "span")

    client.chat.completions.create(
        model="some-model", messages=[{"role": "system", "content": "hello"}]
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


@pytest.mark.asyncio
async def test_span_origin_nonstreaming_chat_async(
    sentry_init,
    capture_items,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = AsyncMock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("transaction", "span")

    await client.chat.completions.create(
        model="some-model", messages=[{"role": "system", "content": "hello"}]
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


def test_span_origin_streaming_chat(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = Stream(cast_to=None, response=None, client=client)
    returned_stream._iterator = [
        ChatCompletionChunk(
            id="1",
            choices=[
                DeltaChoice(
                    index=0, delta=ChoiceDelta(content="hel"), finish_reason=None
                )
            ],
            created=100000,
            model="model-id",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="1",
            choices=[
                DeltaChoice(
                    index=1, delta=ChoiceDelta(content="lo "), finish_reason=None
                )
            ],
            created=100000,
            model="model-id",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="1",
            choices=[
                DeltaChoice(
                    index=2, delta=ChoiceDelta(content="world"), finish_reason="stop"
                )
            ],
            created=100000,
            model="model-id",
            object="chat.completion.chunk",
        ),
    ]
    items = capture_items("transaction", "span")

    client.chat.completions._post = mock.Mock(return_value=returned_stream)
    response_stream = client.chat.completions.create(
        model="some-model", messages=[{"role": "system", "content": "hello"}]
    )

    "".join(map(lambda x: x.choices[0].delta.content, response_stream))

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


@pytest.mark.asyncio
async def test_span_origin_streaming_chat_async(
    sentry_init,
    capture_items,
    async_iterator,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = AsyncStream(cast_to=None, response=None, client=client)
    returned_stream._iterator = async_iterator(
        [
            ChatCompletionChunk(
                id="1",
                choices=[
                    DeltaChoice(
                        index=0, delta=ChoiceDelta(content="hel"), finish_reason=None
                    )
                ],
                created=100000,
                model="model-id",
                object="chat.completion.chunk",
            ),
            ChatCompletionChunk(
                id="1",
                choices=[
                    DeltaChoice(
                        index=1, delta=ChoiceDelta(content="lo "), finish_reason=None
                    )
                ],
                created=100000,
                model="model-id",
                object="chat.completion.chunk",
            ),
            ChatCompletionChunk(
                id="1",
                choices=[
                    DeltaChoice(
                        index=2,
                        delta=ChoiceDelta(content="world"),
                        finish_reason="stop",
                    )
                ],
                created=100000,
                model="model-id",
                object="chat.completion.chunk",
            ),
        ]
    )

    client.chat.completions._post = AsyncMock(return_value=returned_stream)
    items = capture_items("transaction", "span")

    response_stream = await client.chat.completions.create(
        model="some-model", messages=[{"role": "system", "content": "hello"}]
    )
    async for _ in response_stream:
        pass

    # "".join(map(lambda x: x.choices[0].delta.content, response_stream))

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


def test_span_origin_embeddings(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")

    returned_embedding = CreateEmbeddingResponse(
        data=[Embedding(object="embedding", index=0, embedding=[1.0, 2.0, 3.0])],
        model="some-model",
        object="list",
        usage=EmbeddingTokenUsage(
            prompt_tokens=20,
            total_tokens=30,
        ),
    )

    client.embeddings._post = mock.Mock(return_value=returned_embedding)
    items = capture_items("transaction", "span")

    client.embeddings.create(input="hello", model="text-embedding-3-large")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


@pytest.mark.asyncio
async def test_span_origin_embeddings_async(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = AsyncOpenAI(api_key="z")

    returned_embedding = CreateEmbeddingResponse(
        data=[Embedding(object="embedding", index=0, embedding=[1.0, 2.0, 3.0])],
        model="some-model",
        object="list",
        usage=EmbeddingTokenUsage(
            prompt_tokens=20,
            total_tokens=30,
        ),
    )

    client.embeddings._post = AsyncMock(return_value=returned_embedding)
    items = capture_items("transaction", "span")

    await client.embeddings.create(input="hello", model="text-embedding-3-large")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


@pytest.mark.skipif(
    OPENAI_VERSION is None or OPENAI_VERSION < (1, 51, 0),
    reason="Previous versions do not expose cached input tokens. See https://github.com/openai/openai-python/commit/7c8c11158c4e0b63fef495c32447d6e31870073f.",
)
def test_completions_token_usage_with_detailed_fields(
    sentry_init,
    capture_events,
    capture_items,
    nonstreaming_chat_completions_model_response,
    get_model_response,
):
    """Cached and reasoning token counts are extracted from prompt_tokens_details and completion_tokens_details."""
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                prompt_tokens_details=PromptTokensDetails(cached_tokens=5),
                completion_tokens=10,
                completion_tokens_details=CompletionTokensDetails(reasoning_tokens=8),
                total_tokens=30,
            ),
        ),
        serialize_pydantic=True,
    )

    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS] == 5
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_REASONING_OUTPUT_TOKENS] == 8
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30


def test_completions_token_usage_manual_input_counting(
    sentry_init,
    capture_events,
    capture_items,
    nonstreaming_chat_completions_model_response,
    get_model_response,
):
    """When prompt_tokens is missing, input tokens are counted manually from messages."""
    sentry_init(
        integrations=[
            OpenAIIntegration(tiktoken_encoding_name=tiktoken_encoding_if_installed())
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=0,
                completion_tokens=10,
                total_tokens=10,
            ),
        ),
        serialize_pydantic=True,
    )

    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        client.chat.completions.create(
            model="some-model",
            messages=[
                {"content": "one"},
                {"content": "two"},
                {"content": "three"},
            ],
        )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 10
    if tiktoken_encoding_if_installed():
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 3


@pytest.mark.skipif(
    OPENAI_VERSION is None or OPENAI_VERSION < (1, 26, 0),
    reason="Previous versions do not report token usage when streaming. See https://github.com/openai/openai-python/commit/6cc515874f5f4b26b35f408d6afc3c14b4dfe3b0.",
)
def test_completions_token_usage_manual_output_counting_streaming(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_response,
):
    """When completion_tokens is missing, output tokens are counted from streamed content."""
    sentry_init(
        integrations=[
            OpenAIIntegration(tiktoken_encoding_name=tiktoken_encoding_if_installed())
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            streaming_chat_completions_model_response(
                usage=CompletionUsage(
                    prompt_tokens=20,
                    completion_tokens=0,
                    total_tokens=20,
                ),
                message_contents=("one", " two", " three"),
            ),
            include_event_type=False,
        )
    )

    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
        for _ in response_stream:
            pass

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    if tiktoken_encoding_if_installed():
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3


def test_completions_token_usage_manual_output_counting_choices(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
):
    """When completion_tokens is missing, output tokens are counted from response.choices."""
    sentry_init(
        integrations=[
            OpenAIIntegration(tiktoken_encoding_name=tiktoken_encoding_if_installed())
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        ChatCompletion(
            id="chat-id",
            choices=[
                Choice(
                    index=0,
                    finish_reason="stop",
                    message=ChatCompletionMessage(role="assistant", content="one"),
                ),
                Choice(
                    index=1,
                    finish_reason="stop",
                    message=ChatCompletionMessage(role="assistant", content="two"),
                ),
                Choice(
                    index=2,
                    finish_reason="stop",
                    message=ChatCompletionMessage(role="assistant", content="three"),
                ),
            ],
            created=10000000,
            model="gpt-3.5-turbo",
            object="chat.completion",
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=0,
                total_tokens=20,
            ),
        ),
        serialize_pydantic=True,
    )

    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    if tiktoken_encoding_if_installed():
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_token_usage_manual_output_counting_response_output(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    nonstreaming_responses_model_response,
):
    """When output_tokens is missing, output tokens are counted from response.output."""
    sentry_init(
        integrations=[
            OpenAIIntegration(tiktoken_encoding_name=tiktoken_encoding_if_installed())
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        nonstreaming_responses_model_response(
            message_contents=("one", "two", "three"),
            usage=ResponseUsage(
                input_tokens=20,
                input_tokens_details=InputTokensDetails(
                    cached_tokens=0,
                    cache_write_tokens=0,
                ),
                output_tokens=0,
                output_tokens_details=OutputTokensDetails(
                    reasoning_tokens=0,
                ),
                total_tokens=20,
            ),
        ),
        serialize_pydantic=True,
    )

    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        client.responses.create(model="gpt-4o", input="hello")

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    if tiktoken_encoding_if_installed():
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_api_no_sensitive_data(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    client.responses.create(
        model="gpt-4o",
        instructions="You are a coding assistant that talks like a pirate.",
        input="How do I check if a Python object is an instance of a class?",
        max_output_tokens=100,
        temperature=0.7,
        top_p=0.9,
        reasoning={"effort": "high"},
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    assert len(spans) == 1
    expected_attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": False,
        SPANDATA.GEN_AI_PROVIDER_NAME: "openai",
        "gen_ai.usage.input_tokens": 20,
        SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 5,
        "gen_ai.usage.output_tokens": 10,
        SPANDATA.GEN_AI_USAGE_REASONING_OUTPUT_TOKENS: 8,
        "gen_ai.usage.total_tokens": 30,
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
    }

    for attr, value in expected_attributes.items():
        assert spans[0]["attributes"][attr] == value

    assert "gen_ai.system_instructions" not in spans[0]["attributes"]
    assert "gen_ai.request.messages" not in spans[0]["attributes"]
    assert "gen_ai.response.text" not in spans[0]["attributes"]


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_tool_definitions(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    client.responses.create(
        model="gpt-4o",
        input="How do I check if a Python object is an instance of a class?",
        tools=[
            FunctionToolParam(
                type="function",
                name="name",
                description="description",
                parameters={
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "state": {"type": "string"},
                    },
                    "required": ["city", "state"],
                    "additionalProperties": False,
                },
                strict=True,
            ),
            CustomToolParam(type="custom", name="name", description="description"),
            WebSearchToolParam(type="web_search"),
        ],
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert json.loads(spans[0]["attributes"][SPANDATA.GEN_AI_TOOL_DEFINITIONS]) == [
        {
            "type": "function",
            "name": "name",
            "description": "description",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "state": {"type": "string"},
                },
                "required": ["city", "state"],
                "additionalProperties": False,
            },
        },
        {
            "type": "custom",
            "name": "name",
            "description": "description",
        },
        {
            "type": "web_search",
        },
    ]


@pytest.mark.parametrize(
    "instructions,input,expected_system_instructions,expected_request_messages",
    [
        (
            omit,
            "How do I check if a Python object is an instance of a class?",
            None,
            ["How do I check if a Python object is an instance of a class?"],
        ),
        (
            None,
            "How do I check if a Python object is an instance of a class?",
            None,
            ["How do I check if a Python object is an instance of a class?"],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ],
            [
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ],
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": "You are a helpful assistant."},
                        {"type": "input_text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": "You are a helpful assistant."},
                        {"type": "input_text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_api(
    sentry_init,
    capture_items,
    instructions,
    input,
    expected_system_instructions,
    expected_request_messages,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    client.responses.create(
        model="gpt-4o",
        instructions=instructions,
        input=input,
        max_output_tokens=100,
        temperature=0.7,
        top_p=0.9,
        reasoning={"effort": "high"},
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    assert len(spans) == 1

    expected_data = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        SPANDATA.GEN_AI_PROVIDER_NAME: "openai",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": False,
        "gen_ai.usage.input_tokens": 20,
        SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 5,
        "gen_ai.usage.output_tokens": 10,
        SPANDATA.GEN_AI_USAGE_REASONING_OUTPUT_TOKENS: 8,
        "gen_ai.usage.total_tokens": 30,
        "gen_ai.request.messages": safe_serialize(expected_request_messages),
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.text": "the model response",
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
    }

    if expected_system_instructions is not None:
        expected_data["gen_ai.system_instructions"] = safe_serialize(
            expected_system_instructions
        )

    for attr, value in expected_data.items():
        assert spans[0]["attributes"][attr] == value


@pytest.mark.parametrize(
    "conversation, expected_id",
    [
        pytest.param(omit, None, id="omit"),
        pytest.param(None, None, id="none"),
        pytest.param("conv_abc123", "conv_abc123", id="string"),
        pytest.param({"id": "conv_abc123"}, "conv_abc123", id="dict"),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_api_conversation_id(
    sentry_init,
    capture_items,
    conversation,
    expected_id,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    client.responses.create(
        model="gpt-4o",
        input="hello",
        conversation=conversation,
    )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    if expected_id is None:
        assert "gen_ai.conversation.id" not in span["attributes"]
    else:
        assert span["attributes"]["gen_ai.conversation.id"] == expected_id


@pytest.mark.parametrize(
    "reasoning, expected_level",
    [
        pytest.param(omit, None, id="omit"),
        pytest.param(None, None, id="none"),
        pytest.param({"summary": "auto"}, None, id="dict_without_effort"),
        pytest.param({"effort": "high"}, "high", id="dict"),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_api_reasoning_level(
    sentry_init,
    capture_items,
    reasoning,
    expected_level,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    client.responses.create(
        model="gpt-4o",
        input="hello",
        reasoning=reasoning,
    )

    sentry_sdk.flush()
    span = next(item.payload for item in items if item.type == "span")

    if expected_level is None:
        assert SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL not in span["attributes"]
    else:
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL]
            == expected_level
        )


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_error_in_responses_api(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )
    items = capture_items("event", "span")

    with pytest.raises(OpenAIError):
        client.responses.create(
            model="gpt-4o",
            instructions="You are a coding assistant that talks like a pirate.",
            input="How do I check if a Python object is an instance of a class?",
        )

    # make sure the span where the error occurred is captured
    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.op"] == "gen_ai.responses"

    (error_event,) = (item.payload for item in items if item.type == "event")

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "OpenAIError"

    assert error_event["contexts"]["trace"]["trace_id"] == spans[0]["trace_id"]


@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
@pytest.mark.parametrize(
    "instructions,input,expected_system_instructions,expected_request_messages",
    [
        (
            omit,
            "How do I check if a Python object is an instance of a class?",
            None,
            ["How do I check if a Python object is an instance of a class?"],
        ),
        (
            None,
            "How do I check if a Python object is an instance of a class?",
            None,
            ["How do I check if a Python object is an instance of a class?"],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ],
            [
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ],
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": "You are a helpful assistant."},
                        {"type": "input_text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": "You are a helpful assistant."},
                        {"type": "input_text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
        ),
    ],
)
async def test_ai_client_span_responses_async_api(
    sentry_init,
    capture_items,
    instructions,
    input,
    expected_system_instructions,
    expected_request_messages,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    await client.responses.create(
        model="gpt-4o",
        instructions=instructions,
        input=input,
        max_output_tokens=100,
        temperature=0.7,
        top_p=0.9,
        reasoning={"effort": "high"},
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    assert len(spans) == 1

    expected_data = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        "gen_ai.request.messages": safe_serialize(expected_request_messages),
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": False,
        SPANDATA.GEN_AI_PROVIDER_NAME: "openai",
        "gen_ai.usage.input_tokens": 20,
        SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 5,
        "gen_ai.usage.output_tokens": 10,
        SPANDATA.GEN_AI_USAGE_REASONING_OUTPUT_TOKENS: 8,
        "gen_ai.usage.total_tokens": 30,
        "gen_ai.response.text": "the model response",
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
    }

    if expected_system_instructions is not None:
        expected_data["gen_ai.system_instructions"] = safe_serialize(
            expected_system_instructions
        )

    for attr, value in expected_data.items():
        assert spans[0]["attributes"][attr] == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instructions,input,expected_system_instructions,expected_request_messages",
    [
        (
            omit,
            "How do I check if a Python object is an instance of a class?",
            None,
            ["How do I check if a Python object is an instance of a class?"],
        ),
        (
            None,
            "How do I check if a Python object is an instance of a class?",
            None,
            ["How do I check if a Python object is an instance of a class?"],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ],
            [
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
            ],
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": "You are a helpful assistant."},
                        {"type": "input_text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
            ],
        ),
        (
            "You are a coding assistant that talks like a pirate.",
            [
                {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": "You are a helpful assistant."},
                        {"type": "input_text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            [
                {
                    "type": "text",
                    "content": "You are a coding assistant that talks like a pirate.",
                },
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_ai_client_span_streaming_responses_async_api(
    sentry_init,
    capture_items,
    instructions,
    input,
    expected_system_instructions,
    expected_request_messages,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )
    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        result = await client.responses.create(
            model="gpt-4o",
            instructions=instructions,
            input=input,
            stream=True,
            max_output_tokens=100,
            temperature=0.7,
            top_p=0.9,
            reasoning={"effort": "high"},
        )
        async for _ in result:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    spans = [
        span
        for span in spans
        if span["attributes"].get("sentry.op") == OP.GEN_AI_RESPONSES
    ]

    assert len(spans) == 1

    expected_data = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.messages": safe_serialize(expected_request_messages),
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": True,
        SPANDATA.GEN_AI_PROVIDER_NAME: "openai",
        SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK: mock.ANY,
        "gen_ai.usage.input_tokens": 20,
        SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 5,
        "gen_ai.usage.output_tokens": 10,
        SPANDATA.GEN_AI_USAGE_REASONING_OUTPUT_TOKENS: 8,
        "gen_ai.usage.total_tokens": 30,
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.text": "hello world",
        "sentry.environment": "production",
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
    }

    if expected_system_instructions is not None:
        expected_data["gen_ai.system_instructions"] = safe_serialize(
            expected_system_instructions
        )

    for attr, value in expected_data.items():
        assert spans[0]["attributes"][attr] == value


@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_error_in_responses_async_api(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )
    items = capture_items("event", "span")

    with pytest.raises(OpenAIError):
        await client.responses.create(
            model="gpt-4o",
            instructions="You are a coding assistant that talks like a pirate.",
            input="How do I check if a Python object is an instance of a class?",
        )

    # make sure the span where the error occurred is captured
    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.op"] == "gen_ai.responses"

    (error_event,) = (item.payload for item in items if item.type == "event")

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "OpenAIError"

    assert error_event["contexts"]["trace"]["trace_id"] == spans[0]["trace_id"]


if SKIP_RESPONSES_TESTS:
    EXAMPLE_RESPONSES_STREAM = []
else:
    EXAMPLE_RESPONSES_STREAM = [
        ResponseCreatedEvent(
            sequence_number=1,
            type="response.created",
            response=Response(
                id="chat-id",
                created_at=10000000,
                model="response-model-id",
                object="response",
                output=[],
                parallel_tool_calls=False,
                tool_choice="none",
                tools=[],
            ),
        ),
        ResponseTextDeltaEvent(
            item_id="msg_1",
            sequence_number=2,
            type="response.output_text.delta",
            logprobs=[],
            content_index=0,
            output_index=0,
            delta="hel",
        ),
        ResponseTextDeltaEvent(
            item_id="msg_1",
            sequence_number=3,
            type="response.output_text.delta",
            logprobs=[],
            content_index=0,
            output_index=0,
            delta="lo ",
        ),
        ResponseTextDeltaEvent(
            item_id="msg_1",
            sequence_number=4,
            type="response.output_text.delta",
            logprobs=[],
            content_index=0,
            output_index=0,
            delta="world",
        ),
        ResponseCompletedEvent(
            sequence_number=5,
            type="response.completed",
            response=Response(
                id="chat-id",
                created_at=10000000,
                model="response-model-id",
                object="response",
                output=[],
                parallel_tool_calls=False,
                tool_choice="none",
                tools=[],
                usage=ResponseUsage(
                    input_tokens=20,
                    input_tokens_details=InputTokensDetails(
                        cached_tokens=5,
                        cache_write_tokens=0,
                    ),
                    output_tokens=10,
                    output_tokens_details=OutputTokensDetails(
                        reasoning_tokens=8,
                    ),
                    total_tokens=30,
                ),
            ),
        ),
    ]


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_streaming_responses_api(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            EXAMPLE_RESPONSES_STREAM,
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.responses.create(
            model="some-model",
            input="hello",
            stream=True,
            max_output_tokens=100,
            temperature=0.7,
            top_p=0.9,
            reasoning={"effort": "high"},
        )

        response_string = ""
        for item in response_stream:
            if hasattr(item, "delta"):
                response_string += item.delta

    assert response_string == "hello world"

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.responses"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES] == '["hello"]'
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "hello world"

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_streaming_responses_api_no_sensitive_data(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            EXAMPLE_RESPONSES_STREAM,
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.responses.create(
            model="some-model",
            input="hello",
            stream=True,
            max_output_tokens=100,
            temperature=0.7,
            top_p=0.9,
            reasoning={"effort": "high"},
        )

        response_string = ""
        for item in response_stream:
            if hasattr(item, "delta"):
                response_string += item.delta

    assert response_string == "hello world"

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.responses"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_streaming_responses_api_async(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )
    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.responses.create(
            model="some-model",
            input="hello",
            stream=True,
            max_output_tokens=100,
            temperature=0.7,
            top_p=0.9,
            reasoning={"effort": "high"},
        )

        response_string = ""
        async for item in response_stream:
            if hasattr(item, "delta"):
                response_string += item.delta

    assert response_string == "hello world"

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.responses"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES] == '["hello"]'
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "hello world"

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_streaming_responses_api_async_no_sensitive_data(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": False,
                "outputs": False,
            }
        },
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )
    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.responses.create(
            model="some-model",
            input="hello",
            stream=True,
            max_output_tokens=100,
            temperature=0.7,
            top_p=0.9,
            reasoning={"effort": "high"},
        )

        response_string = ""
        async for item in response_stream:
            if hasattr(item, "delta"):
                response_string += item.delta

    assert response_string == "hello world"

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.responses"
    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


# Feature added in https://github.com/openai/openai-python/pull/1952
@pytest.mark.skipif(
    OPENAI_VERSION is None or OPENAI_VERSION < (1, 58, 0),
    reason="OpenAI versions <1.58.0 do not support the reasoning_effort parameter.",
)
@pytest.mark.parametrize(
    "reasoning_effort,expected_level",
    [
        pytest.param(omit, None, id="omit"),
        pytest.param(None, None, id="none"),
        pytest.param("high", "high", id="high"),
        pytest.param("minimal", "minimal", id="minimal"),
    ],
)
def test_chat_completion_reasoning_level(
    sentry_init,
    capture_items,
    reasoning_effort,
    expected_level,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("span")

    client.chat.completions.create(
        model="some-model",
        messages=[{"role": "system", "content": "hello"}],
        reasoning_effort=reasoning_effort,
    )

    sentry_sdk.flush()
    span = next(item.payload for item in items if item.type == "span")

    if expected_level is None:
        assert SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL not in span["attributes"]
    else:
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL]
            == expected_level
        )


# Test messages with mixed roles including "ai" that should be mapped to "assistant"
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
def test_openai_message_role_mapping(
    sentry_init,
    capture_items,
    test_message,
    expected_role,
    nonstreaming_chat_completions_model_response,
):
    """Test that OpenAI integration properly maps message roles like 'ai' to 'assistant'"""

    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        data_collection={
            "gen_ai": {
                "inputs": True,
                "outputs": True,
            }
        },
    )

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        return_value=nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="gpt-3.5-turbo",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )

    test_messages = [test_message]
    items = capture_items("span")

    client.chat.completions.create(model="test-model", messages=test_messages)

    # Verify that the span was created correctly
    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]

    stored_messages = json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    assert len(stored_messages) == 1
    assert stored_messages[0]["role"] == expected_role


# noinspection PyTypeChecker
def test_streaming_chat_completion_ttft(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """
    Test that streaming chat completions capture time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            [
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=0,
                            delta=ChoiceDelta(content="Hello"),
                            finish_reason=None,
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
                ChatCompletionChunk(
                    id="1",
                    choices=[
                        DeltaChoice(
                            index=0,
                            delta=ChoiceDelta(content=" world"),
                            finish_reason="stop",
                        )
                    ],
                    created=100000,
                    model="model-id",
                    object="chat.completion.chunk",
                ),
            ],
            include_event_type=False,
        ),
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "Say hello"}],
            stream=True,
        )
        # Consume the stream
        for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"

    # Verify TTFT is captured
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK]

    assert isinstance(ttft, float)
    assert ttft > 0


# noinspection PyTypeChecker
@pytest.mark.asyncio
async def test_streaming_chat_completion_ttft_async(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    """
    Test that async streaming chat completions capture time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(
            server_side_event_chunks(
                [
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=0,
                                delta=ChoiceDelta(content="Hello"),
                                finish_reason=None,
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                    ChatCompletionChunk(
                        id="1",
                        choices=[
                            DeltaChoice(
                                index=0,
                                delta=ChoiceDelta(content=" world"),
                                finish_reason="stop",
                            )
                        ],
                        created=100000,
                        model="model-id",
                        object="chat.completion.chunk",
                    ),
                ],
                include_event_type=False,
            ),
        )
    )
    items = capture_items("span")

    with mock.patch.object(
        client.chat._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "Say hello"}],
            stream=True,
        )
        # Consume the stream
        async for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"

    # Verify TTFT is captured
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK]

    assert isinstance(ttft, float)
    assert ttft > 0


# noinspection PyTypeChecker
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_streaming_responses_api_ttft(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """
    Test that streaming responses API captures time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(EXAMPLE_RESPONSES_STREAM)
    )
    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = client.responses.create(
            model="some-model",
            input="hello",
            stream=True,
        )
        # Consume the stream
        for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.responses"

    # Verify TTFT is captured
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK]

    assert isinstance(ttft, float)
    assert ttft > 0


# noinspection PyTypeChecker
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_streaming_responses_api_ttft_async(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    """
    Test that async streaming responses API captures time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )
    items = capture_items("span")

    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ):
        response_stream = await client.responses.create(
            model="some-model",
            input="hello",
            stream=True,
        )
        # Consume the stream
        async for _ in response_stream:
            pass

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.responses"

    # Verify TTFT is captured
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK]

    assert isinstance(ttft, float)
    assert ttft > 0

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
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputRefusal,
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

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.openai import (
    OpenAIIntegration,
    _calculate_completions_token_usage,
    _calculate_responses_token_usage,
)
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
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
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


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_nonstreaming_chat_completion_no_prompts(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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


@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the tools parameter.",
)
@pytest.mark.parametrize(
    "data_collection,expected_present,expected_absent",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            {
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: json.dumps(
                    [{"type": "text", "content": "You are a helpful assistant."}]
                ),
                SPANDATA.GEN_AI_REQUEST_MESSAGES: safe_serialize(
                    [{"role": "user", "content": "hello"}]
                ),
                SPANDATA.GEN_AI_TOOL_DEFINITIONS: safe_serialize(EXAMPLE_TOOLS),
            },
            [],
            id="gen-ai-inputs-enabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            {},
            [
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
                SPANDATA.GEN_AI_REQUEST_MESSAGES,
                SPANDATA.GEN_AI_TOOL_DEFINITIONS,
            ],
            id="gen-ai-inputs-disabled",
        ),
        pytest.param(
            {},
            {
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: json.dumps(
                    [{"type": "text", "content": "You are a helpful assistant."}]
                ),
                SPANDATA.GEN_AI_REQUEST_MESSAGES: safe_serialize(
                    [{"role": "user", "content": "hello"}]
                ),
                SPANDATA.GEN_AI_TOOL_DEFINITIONS: safe_serialize(EXAMPLE_TOOLS),
            },
            [],
            id="gen-ai-omitted-defaults-to-enabled",
        ),
    ],
)
def test_completions_api_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    expected_present,
    expected_absent,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        _experiments={"data_collection": data_collection},
        trace_lifecycle="stream",
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

    create_kwargs = {
        "model": "some-model",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 100,
        "presence_penalty": 0.1,
        "frequency_penalty": 0.2,
        "temperature": 0.7,
        "top_p": 0.9,
        "tools": EXAMPLE_COMPLETIONS_TOOLS,
    }
    items = capture_items("span")

    with start_transaction(name="openai tx"):
        client.chat.completions.create(**create_kwargs)

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    span_data = span["attributes"]

    assert span_data[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span_data[SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert span_data[SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert span_data[SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span_data[SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span_data[SPANDATA.GEN_AI_SYSTEM] == "openai"

    for key, value in expected_present.items():
        assert span_data[key] == value

    for key in expected_absent:
        assert key not in span_data


@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_output",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-disabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-enabled",
        ),
        pytest.param(
            {},
            False,
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            False,
            False,
            id="gen-ai-outputs-disabled-and-pii-disabled",
        ),
        pytest.param(
            None,
            False,
            False,
            id="no-gen-ai-data-collection-falls-back-to-send-default-pii",
        ),
        pytest.param(
            None,
            True,
            True,
            id="no-gen-ai-data-collection-pii-enabled-collects",
        ),
    ],
)
def test_completions_api_data_collection_outputs(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    expect_output,
    nonstreaming_chat_completions_model_response,
):
    init_kwargs = {
        "integrations": [OpenAIIntegration()],
        "disabled_integrations": [StdlibIntegration],
        "traces_sample_rate": 1.0,
        "send_default_pii": send_default_pii,
        "trace_lifecycle": "stream",
    }
    if data_collection is not None:
        init_kwargs["_experiments"] = {"data_collection": data_collection}

    sentry_init(**init_kwargs)

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

    with start_transaction(name="openai tx"):
        client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    span_data = span["attributes"]

    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "gpt-3.5-turbo"

    if expect_output:
        assert "the model response" in span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_output",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-disabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-enabled",
        ),
        pytest.param(
            {},
            False,
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            False,
            False,
            id="gen-ai-outputs-disabled-and-pii-disabled",
        ),
        pytest.param(
            None,
            False,
            False,
            id="no-gen-ai-data-collection-falls-back-to-send-default-pii",
        ),
        pytest.param(
            None,
            True,
            True,
            id="no-gen-ai-data-collection-pii-enabled-collects",
        ),
    ],
)
async def test_completions_api_data_collection_outputs_async(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    expect_output,
    nonstreaming_chat_completions_model_response,
):
    init_kwargs = {
        "integrations": [OpenAIIntegration()],
        "disabled_integrations": [StdlibIntegration],
        "traces_sample_rate": 1.0,
        "send_default_pii": send_default_pii,
        "trace_lifecycle": "stream",
    }
    if data_collection is not None:
        init_kwargs["_experiments"] = {"data_collection": data_collection}

    sentry_init(**init_kwargs)

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

    with start_transaction(name="openai tx"):
        await client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    span_data = span["attributes"]

    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "gpt-3.5-turbo"

    if expect_output:
        assert "the model response" in span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


def test_completions_api_data_collection_outputs_empty_choices(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        _experiments={"data_collection": {"gen_ai": {"outputs": True}}},
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        return_value=ChatCompletion(
            id="chat-id",
            choices=[],
            created=10000000,
            model="gpt-3.5-turbo",
            object="chat.completion",
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )
    )
    items = capture_items("span")

    with start_transaction(name="openai tx"):
        client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    span_data = span["attributes"]

    # No choices means no output data, even with outputs collection enabled
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


@pytest.mark.parametrize(
    "data_collection,expect_output",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            True,
            id="gen-ai-outputs-enabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            False,
            id="gen-ai-outputs-disabled",
        ),
        pytest.param(
            {},
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
    ],
)
def test_streaming_chat_completion_data_collection_outputs(
    sentry_init,
    capture_items,
    data_collection,
    expect_output,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        _experiments={"data_collection": data_collection},
        trace_lifecycle="stream",
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
                            delta=ChoiceDelta(content="hello"),
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
    ), start_transaction(name="openai tx"):
        response_stream = client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
        response_string = "".join(
            map(lambda x: x.choices[0].delta.content, response_stream)
        )

    assert response_string == "hello"
    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    span_data = span["attributes"]

    if expect_output:
        assert "hello" in span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection,expect_output",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            True,
            id="gen-ai-outputs-enabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            False,
            id="gen-ai-outputs-disabled",
        ),
        pytest.param(
            {},
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
    ],
)
async def test_streaming_chat_completion_data_collection_outputs_async(
    sentry_init,
    capture_items,
    data_collection,
    expect_output,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        _experiments={"data_collection": data_collection},
        trace_lifecycle="stream",
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
                                delta=ChoiceDelta(content="hello"),
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
    ), start_transaction(name="openai tx"):
        response_stream = await client.chat.completions.create(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
        response_string = ""
        async for x in response_stream:
            response_string += x.choices[0].delta.content

    assert response_string == "hello"
    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    span_data = span["attributes"]

    if expect_output:
        assert "hello" in span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_nonstreaming_chat_completion_async_no_prompts(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_streaming_chat_completion_no_prompts(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
        integrations=[OpenAIIntegration(include_prompts=False)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
        integrations=[OpenAIIntegration(include_prompts=False)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
        integrations=[OpenAIIntegration(include_prompts=False)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
        integrations=[OpenAIIntegration(include_prompts=False)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
                include_prompts=True,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_streaming_chat_completion_async_no_prompts(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
                include_prompts=True,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
        trace_lifecycle="stream",
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
        trace_lifecycle="stream",
    )
    items = capture_items("event", "span")

    with start_transaction(name="test"):
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
        trace_lifecycle="stream",
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


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_embeddings_create_no_pii(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
        response = client.embeddings.create(
            input="hello", model="text-embedding-3-large"
        )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
        response = client.embeddings.create(
            input=get_input(), model="text-embedding-3-large"
        )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
        == expected_embeddings_input
    )

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


def _collect_embeddings_span_data(capture_events, capture_items, create):
    items = capture_items("span")

    with start_transaction(name="openai tx"):
        response = create()

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    return span["attributes"]


@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_input",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            False,
            True,
            id="gen-ai-inputs-enabled-overrides-pii-disabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            True,
            False,
            id="gen-ai-inputs-disabled-overrides-pii-enabled",
        ),
        pytest.param(
            {},
            False,
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            False,
            False,
            id="gen-ai-inputs-disabled-and-pii-disabled",
        ),
        pytest.param(
            None,
            False,
            False,
            id="no-gen-ai-data-collection-falls-back-to-send-default-pii",
        ),
    ],
)
def test_embeddings_create_data_collection(
    sentry_init,
    capture_events,
    capture_items,
    data_collection,
    send_default_pii,
    expect_input,
):
    init_kwargs = {
        "integrations": [OpenAIIntegration()],
        "disabled_integrations": [StdlibIntegration],
        "traces_sample_rate": 1.0,
        "send_default_pii": send_default_pii,
        "trace_lifecycle": "stream",
    }

    sentry_init_kwargs = dict(init_kwargs)
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}

    sentry_init(**sentry_init_kwargs)

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

    span_data = _collect_embeddings_span_data(
        capture_events,
        capture_items,
        lambda: client.embeddings.create(input="hello", model="text-embedding-3-large"),
    )

    assert span_data[SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span_data[SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    if expect_input:
        assert json.loads(span_data[SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == ["hello"]
    else:
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span_data

    assert span_data["gen_ai.usage.input_tokens"] == 20
    assert span_data["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize(
    "get_input",
    [
        lambda: "hello",
        lambda: ["First text", "Second text"],
        lambda: iter(["First text", "Second text"]),
        lambda: [5, 8, 13, 21, 34],
        lambda: [[5, 8, 13], [8, 13, 21]],
        lambda: {"text": "hello"},
    ],
)
def test_embeddings_create_data_collection_inputs_disabled_input_shapes(
    sentry_init,
    capture_events,
    capture_items,
    get_input,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
        _experiments={"data_collection": {"gen_ai": {"inputs": False}}},
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

    span_data = _collect_embeddings_span_data(
        capture_events,
        capture_items,
        lambda: client.embeddings.create(
            input=get_input(), model="text-embedding-3-large"
        ),
    )

    assert span_data[SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"
    assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_embeddings_create_async_no_pii(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
        response = await client.embeddings.create(
            input="hello", model="text-embedding-3-large"
        )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
        response = await client.embeddings.create(
            input=get_input(), model="text-embedding-3-large"
        )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    assert (
        json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
        == expected_embeddings_input
    )

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_input",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            False,
            True,
            id="gen-ai-inputs-enabled-overrides-pii-disabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            True,
            False,
            id="gen-ai-inputs-disabled-overrides-pii-enabled",
        ),
        pytest.param(
            {},
            False,
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            False,
            False,
            id="no-experiment-falls-back-to-pii",
        ),
    ],
)
async def test_embeddings_create_async_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    expect_input,
):
    init_kwargs = {
        "integrations": [OpenAIIntegration()],
        "disabled_integrations": [StdlibIntegration],
        "traces_sample_rate": 1.0,
        "send_default_pii": send_default_pii,
        "trace_lifecycle": "stream",
    }

    sentry_init_kwargs = dict(init_kwargs)
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}

    sentry_init(**sentry_init_kwargs)

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

    with start_transaction(name="openai tx"):
        response = await client.embeddings.create(
            input="hello", model="text-embedding-3-large"
        )

    assert len(response.data[0].embedding) == 3

    sentry_sdk.flush()
    span = next(item.payload for item in items)
    assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
    span_data = span["attributes"]

    assert span_data[SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span_data[SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

    if expect_input:
        assert json.loads(span_data[SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == ["hello"]
    else:
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span_data

    assert span_data["gen_ai.usage.input_tokens"] == 20
    assert span_data["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_embeddings_create_raises_error(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_embeddings_create_raises_error_async(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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
        trace_lifecycle="stream",
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

    with sentry_sdk.traces.start_span(name="openai tx"):
        client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
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
        trace_lifecycle="stream",
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

    with sentry_sdk.traces.start_span(name="openai tx"):
        await client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


def test_span_origin_streaming_chat(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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
    with sentry_sdk.traces.start_span(name="openai tx"):
        response_stream = client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

        "".join(map(lambda x: x.choices[0].delta.content, response_stream))

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
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
        trace_lifecycle="stream",
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

    with sentry_sdk.traces.start_span(name="openai tx"):
        response_stream = await client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )
        async for _ in response_stream:
            pass

        # "".join(map(lambda x: x.choices[0].delta.content, response_stream))

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


def test_span_origin_embeddings(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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

    with sentry_sdk.traces.start_span(name="openai tx"):
        client.embeddings.create(input="hello", model="text-embedding-3-large")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
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
        trace_lifecycle="stream",
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

    with sentry_sdk.traces.start_span(name="openai tx"):
        await client.embeddings.create(input="hello", model="text-embedding-3-large")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"


def test_completions_token_usage_from_response():
    """Token counts are extracted from response.usage using Completions API field names."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.completion_tokens = 10
    response.usage.prompt_tokens = 20
    response.usage.total_tokens = 30
    messages = []
    streaming_message_responses = []

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_completions_token_usage(
            messages=messages,
            response=response,
            span=span,
            streaming_message_responses=streaming_message_responses,
            streaming_message_total_token_usage=None,
            count_tokens=count_tokens,
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=None,
            output_tokens=10,
            output_tokens_reasoning=None,
            total_tokens=30,
        )


def test_completions_token_usage_with_detailed_fields():
    """Cached and reasoning token counts are extracted from prompt_tokens_details and completion_tokens_details."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.prompt_tokens = 20
    response.usage.prompt_tokens_details = mock.MagicMock()
    response.usage.prompt_tokens_details.cached_tokens = 5
    response.usage.completion_tokens = 10
    response.usage.completion_tokens_details = mock.MagicMock()
    response.usage.completion_tokens_details.reasoning_tokens = 8
    response.usage.total_tokens = 30

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_completions_token_usage(
            messages=[],
            response=response,
            span=span,
            streaming_message_responses=[],
            streaming_message_total_token_usage=None,
            count_tokens=count_tokens,
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=5,
            output_tokens=10,
            output_tokens_reasoning=8,
            total_tokens=30,
        )


def test_completions_token_usage_manual_input_counting():
    """When prompt_tokens is missing, input tokens are counted manually from messages."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.completion_tokens = 10
    response.usage.total_tokens = 10
    messages = [
        {"content": "one"},
        {"content": "two"},
        {"content": "three"},
    ]
    streaming_message_responses = []

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_completions_token_usage(
            messages=messages,
            response=response,
            span=span,
            streaming_message_responses=streaming_message_responses,
            streaming_message_total_token_usage=None,
            count_tokens=count_tokens,
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=11,
            input_tokens_cached=None,
            output_tokens=10,
            output_tokens_reasoning=None,
            total_tokens=10,
        )


def test_completions_token_usage_manual_output_counting_streaming():
    """When completion_tokens is missing, output tokens are counted from streaming responses."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.prompt_tokens = 20
    response.usage.total_tokens = 20
    messages = []
    streaming_message_responses = [
        "one",
        "two",
        "three",
    ]

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_completions_token_usage(
            messages=messages,
            response=response,
            span=span,
            streaming_message_responses=streaming_message_responses,
            streaming_message_total_token_usage=None,
            count_tokens=count_tokens,
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=None,
            output_tokens=11,
            output_tokens_reasoning=None,
            total_tokens=20,
        )


def test_completions_token_usage_manual_output_counting_choices():
    """When completion_tokens is missing, output tokens are counted from response.choices."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.prompt_tokens = 20
    response.usage.total_tokens = 20
    response.choices = [
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
    ]
    messages = []
    streaming_message_responses = None

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_completions_token_usage(
            messages=messages,
            response=response,
            span=span,
            streaming_message_responses=streaming_message_responses,
            streaming_message_total_token_usage=None,
            count_tokens=count_tokens,
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=None,
            output_tokens=11,
            output_tokens_reasoning=None,
            total_tokens=20,
        )


def test_completions_token_usage_no_usage_data():
    """When response has no usage data and no streaming responses, all tokens are None."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    messages = []
    streaming_message_responses = None

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_completions_token_usage(
            messages=messages,
            response=response,
            span=span,
            streaming_message_responses=streaming_message_responses,
            streaming_message_total_token_usage=None,
            count_tokens=count_tokens,
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=None,
            input_tokens_cached=None,
            output_tokens=None,
            output_tokens_reasoning=None,
            total_tokens=None,
        )


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_token_usage_from_response():
    """Token counts including cached and reasoning tokens are extracted from Responses API."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.input_tokens = 20
    response.usage.input_tokens_details = mock.MagicMock()
    response.usage.input_tokens_details.cached_tokens = 5
    response.usage.output_tokens = 10
    response.usage.output_tokens_details = mock.MagicMock()
    response.usage.output_tokens_details.reasoning_tokens = 8
    response.usage.total_tokens = 30
    input = []

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_responses_token_usage(input, response, span, None, count_tokens)
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=5,
            output_tokens=10,
            output_tokens_reasoning=8,
            total_tokens=30,
        )


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_token_usage_no_usage_data():
    """When Responses API response has no usage data, all tokens are None."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = None
    input = []
    streaming_message_responses = None

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_responses_token_usage(
            input, response, span, streaming_message_responses, count_tokens
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=None,
            input_tokens_cached=None,
            output_tokens=None,
            output_tokens_reasoning=None,
            total_tokens=None,
        )


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_token_usage_manual_output_counting_response_output():
    """When output_tokens is missing, output tokens are counted from response.output."""
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.input_tokens = 20
    response.usage.total_tokens = 20
    response.output = [
        ResponseOutputMessage(
            id="msg-1",
            content=[
                ResponseOutputText(
                    annotations=[],
                    text="one",
                    type="output_text",
                ),
            ],
            role="assistant",
            status="completed",
            type="message",
        ),
        ResponseOutputMessage(
            id="msg-2",
            content=[
                ResponseOutputText(
                    annotations=[],
                    text="two",
                    type="output_text",
                ),
                ResponseOutputText(
                    annotations=[],
                    text="three",
                    type="output_text",
                ),
            ],
            role="assistant",
            status="completed",
            type="message",
        ),
    ]
    input = []
    streaming_message_responses = None

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_responses_token_usage(
            input, response, span, streaming_message_responses, count_tokens
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=None,
            output_tokens=11,
            output_tokens_reasoning=None,
            total_tokens=20,
        )


@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_api_no_pii(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="openai tx"):
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

    assert len(spans) == 2
    expected_attributes = {
        "gen_ai.operation.name": "responses",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": False,
        "gen_ai.system": "openai",
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.input_tokens.cached": 5,
        "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.output_tokens.reasoning": 8,
        "gen_ai.usage.total_tokens": 30,
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
        "sentry.segment.name": "openai tx",
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
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="openai tx"):
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="openai tx"):
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

    assert len(spans) == 2

    expected_data = {
        "gen_ai.operation.name": "responses",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        "gen_ai.system": "openai",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": False,
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.input_tokens.cached": 5,
        "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.output_tokens.reasoning": 8,
        "gen_ai.usage.total_tokens": 30,
        "gen_ai.request.messages": safe_serialize(expected_request_messages),
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.text": "the model response",
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
        "sentry.segment.name": "openai tx",
    }

    if expected_system_instructions is not None:
        expected_data["gen_ai.system_instructions"] = safe_serialize(
            expected_system_instructions
        )

    for attr, value in expected_data.items():
        assert spans[0]["attributes"][attr] == value


@pytest.mark.parametrize(
    "data_collection,extra_kwargs,expected_present,expected_absent,include_prompts",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            {
                "instructions": "You are a coding assistant that talks like a pirate.",
                "input": "How do I check if a Python object is an instance of a class?",
                "tools": EXAMPLE_TOOLS,
            },
            {
                SPANDATA.GEN_AI_REQUEST_MESSAGES: safe_serialize(
                    ["How do I check if a Python object is an instance of a class?"]
                ),
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: safe_serialize(
                    [
                        {
                            "type": "text",
                            "content": "You are a coding assistant that talks like a pirate.",
                        }
                    ]
                ),
                SPANDATA.GEN_AI_TOOL_DEFINITIONS: safe_serialize(EXAMPLE_TOOLS),
            },
            [],
            True,
            id="gen-ai-inputs-enabled-string-input",
        ),
        pytest.param(
            {"gen_ai": {"inputs": True}},
            {
                "instructions": "You are a coding assistant that talks like a pirate.",
            },
            {
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: safe_serialize(
                    [
                        {
                            "type": "text",
                            "content": "You are a coding assistant that talks like a pirate.",
                        }
                    ]
                ),
            },
            [
                SPANDATA.GEN_AI_REQUEST_MESSAGES,
                SPANDATA.GEN_AI_TOOL_DEFINITIONS,
            ],
            True,
            id="gen-ai-inputs-enabled-instructions-only",
        ),
        pytest.param(
            {"gen_ai": {"inputs": True}},
            {
                "instructions": "You are a coding assistant that talks like a pirate.",
                "input": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "hello"},
                ],
            },
            {
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: safe_serialize(
                    [
                        {
                            "type": "text",
                            "content": "You are a coding assistant that talks like a pirate.",
                        },
                        {"type": "text", "content": "You are a helpful assistant."},
                    ]
                ),
                SPANDATA.GEN_AI_REQUEST_MESSAGES: safe_serialize(
                    [{"role": "user", "content": "hello"}]
                ),
            },
            [SPANDATA.GEN_AI_TOOL_DEFINITIONS],
            True,
            id="gen-ai-inputs-enabled-list-input-with-system-message",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            {
                "instructions": "You are a coding assistant that talks like a pirate.",
                "input": "How do I check if a Python object is an instance of a class?",
                "tools": EXAMPLE_TOOLS,
            },
            {},
            [
                SPANDATA.GEN_AI_REQUEST_MESSAGES,
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
                SPANDATA.GEN_AI_TOOL_DEFINITIONS,
            ],
            True,
            id="gen-ai-inputs-disabled",
        ),
        pytest.param(
            {},
            {
                "input": "How do I check if a Python object is an instance of a class?",
                "tools": EXAMPLE_TOOLS,
            },
            {
                SPANDATA.GEN_AI_REQUEST_MESSAGES: safe_serialize(
                    ["How do I check if a Python object is an instance of a class?"]
                ),
                SPANDATA.GEN_AI_TOOL_DEFINITIONS: safe_serialize(EXAMPLE_TOOLS),
            },
            [SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS],
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": True}},
            {},
            {},
            [
                SPANDATA.GEN_AI_REQUEST_MESSAGES,
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
                SPANDATA.GEN_AI_TOOL_DEFINITIONS,
            ],
            True,
            id="gen-ai-inputs-enabled-no-input-provided",
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_api_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    extra_kwargs,
    expected_present,
    expected_absent,
    include_prompts,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        _experiments={"data_collection": data_collection},
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)

    create_kwargs = {
        "model": "gpt-4o",
        "max_output_tokens": 100,
        "temperature": 0.7,
        "top_p": 0.9,
        "reasoning": {"effort": "high"},
    }
    create_kwargs.update(extra_kwargs)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="openai tx"):
        client.responses.create(**create_kwargs)

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    assert len(spans) == 2
    span_data = spans[0]["attributes"]

    # Non-input data is always collected, regardless of data collection config
    assert span_data["gen_ai.operation.name"] == "responses"
    assert span_data["gen_ai.request.model"] == "gpt-4o"
    assert span_data["gen_ai.request.max_tokens"] == 100
    assert span_data["gen_ai.request.temperature"] == 0.7
    assert span_data["gen_ai.request.top_p"] == 0.9
    assert span_data["gen_ai.request.reasoning.level"] == "high"
    assert span_data["gen_ai.system"] == "openai"

    for key, value in expected_present.items():
        assert span_data[key] == value

    for key in expected_absent:
        assert key not in span_data


def _make_responses_api_output_message(content):
    return ResponseOutputMessage(
        id="message-id",
        content=content,
        role="assistant",
        status="completed",
        type="message",
    )


def _make_responses_api_function_call():
    return ResponseFunctionToolCall(
        id="fc-id",
        call_id="call_123",
        name="get_current_weather",
        arguments='{"location": "San Francisco, CA"}',
        type="function_call",
    )


def _make_responses_api_response(output):
    return Response(
        id="chat-id",
        output=output,
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


def _collect_responses_span_data(capture_events, capture_items, create):
    items = capture_items("span")

    with start_transaction(name="openai tx"):
        create()

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    return span["attributes"]


@pytest.mark.parametrize(
    "data_collection,send_default_pii,expect_output",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-disabled",
        ),
        pytest.param(
            {},
            False,
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False, "outputs": False}},
            False,
            False,
            id="gen-ai-inputs-and-outputs-disabled-and-pii-disabled",
        ),
        pytest.param(
            None,
            False,
            False,
            id="no-gen-ai-data-collection-falls-back-to-send-default-pii",
        ),
        pytest.param(
            None,
            True,
            True,
            id="no-gen-ai-data-collection-pii-enabled-collects",
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_api_data_collection_outputs(
    sentry_init,
    capture_events,
    capture_items,
    data_collection,
    send_default_pii,
    expect_output,
):
    init_kwargs = {
        "integrations": [OpenAIIntegration()],
        "disabled_integrations": [StdlibIntegration],
        "traces_sample_rate": 1.0,
        "send_default_pii": send_default_pii,
        "trace_lifecycle": "stream",
    }
    if data_collection is not None:
        init_kwargs["_experiments"] = {"data_collection": data_collection}

    sentry_init(**init_kwargs)

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(
        return_value=_make_responses_api_response(
            output=[
                _make_responses_api_output_message(
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="the model response",
                            type="output_text",
                        ),
                    ]
                ),
                _make_responses_api_function_call(),
            ]
        )
    )

    span_data = _collect_responses_span_data(
        capture_events,
        capture_items,
        lambda: client.responses.create(model="gpt-4o", input="hello"),
    )

    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

    if expect_output:
        assert "the model response" in span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert "get_current_weather" in span_data[SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in span_data


@pytest.mark.parametrize(
    "get_output,expect_text,expect_tool_calls",
    [
        pytest.param(
            lambda: [
                _make_responses_api_output_message(
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="the model response",
                            type="output_text",
                        ),
                    ]
                ),
            ],
            True,
            False,
            id="message-only",
        ),
        pytest.param(
            lambda: [_make_responses_api_function_call()],
            False,
            True,
            id="function-call-only",
        ),
        pytest.param(
            lambda: [
                _make_responses_api_output_message(
                    content=[
                        ResponseOutputRefusal(
                            refusal="I cannot help with that.",
                            type="refusal",
                        ),
                    ]
                ),
            ],
            True,
            False,
            id="non-text-content-falls-back-to-dict",
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_responses_api_data_collection_outputs_shapes(
    sentry_init,
    capture_events,
    capture_items,
    get_output,
    expect_text,
    expect_tool_calls,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        _experiments={"data_collection": {"gen_ai": {"outputs": True}}},
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(
        return_value=_make_responses_api_response(output=get_output())
    )

    span_data = _collect_responses_span_data(
        capture_events,
        capture_items,
        lambda: client.responses.create(model="gpt-4o", input="hello"),
    )

    if expect_text:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in span_data
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data

    if expect_tool_calls:
        assert "get_current_weather" in span_data[SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in span_data


@pytest.mark.parametrize(
    "data_collection,expect_output",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            True,
            id="gen-ai-outputs-enabled",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            False,
            id="gen-ai-outputs-disabled",
        ),
        pytest.param(
            {},
            True,
            id="gen-ai-omitted-defaults-to-enabled",
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_streaming_responses_api_data_collection_outputs(
    sentry_init,
    capture_items,
    data_collection,
    expect_output,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        _experiments={"data_collection": data_collection},
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
        response_stream = client.responses.create(
            model="some-model",
            input="hello",
            stream=True,
        )
        response_string = ""
        for item in response_stream:
            if hasattr(item, "delta"):
                response_string += item.delta

    assert response_string == "hello world"
    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    span_data = span["attributes"]

    if expect_output:
        assert "hello world" in span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


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
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    with start_transaction(name="openai tx"):
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
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    with start_transaction(name="openai tx"):
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )
    items = capture_items("event", "span")

    with sentry_sdk.traces.start_span(name="openai tx"), pytest.raises(OpenAIError):
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

    assert spans[1]["is_segment"] is True
    assert error_event["contexts"]["trace"]["trace_id"] == spans[1]["trace_id"]


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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(return_value=EXAMPLE_RESPONSE)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="openai tx"):
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

    assert len(spans) == 2

    expected_data = {
        "gen_ai.operation.name": "responses",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        "gen_ai.request.messages": safe_serialize(expected_request_messages),
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": False,
        "gen_ai.system": "openai",
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.input_tokens.cached": 5,
        "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.output_tokens.reasoning": 8,
        "gen_ai.usage.total_tokens": 30,
        "gen_ai.response.text": "the model response",
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
        "sentry.segment.name": "openai tx",
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )
    items = capture_items("span")

    ctx = sentry_sdk.traces.start_span(name="openai tx")
    with mock.patch.object(
        client.responses._client._client,
        "send",
        return_value=returned_stream,
    ), ctx:
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
        "gen_ai.operation.name": "responses",
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.messages": safe_serialize(expected_request_messages),
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.request.reasoning.level": "high",
        "gen_ai.response.model": "response-model-id",
        "gen_ai.response.streaming": True,
        "gen_ai.system": "openai",
        "gen_ai.response.time_to_first_token": mock.ANY,
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.input_tokens.cached": 5,
        "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.output_tokens.reasoning": 8,
        "gen_ai.usage.total_tokens": 30,
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.response.text": "hello world",
        "sentry.environment": "production",
        "sentry.op": "gen_ai.responses",
        "sentry.origin": "auto.ai.openai",
        "sentry.segment.name": "openai tx",
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )
    items = capture_items("event", "span")

    with sentry_sdk.traces.start_span(name="openai tx"), pytest.raises(OpenAIError):
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

    assert spans[1]["is_segment"] is True
    assert error_event["contexts"]["trace"]["trace_id"] == spans[1]["trace_id"]


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


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_streaming_responses_api(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

    if send_default_pii and include_prompts:
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES] == '["hello"]'
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "hello world"
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_streaming_responses_api_async(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
            )
        ],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

    if send_default_pii and include_prompts:
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES] == '["hello"]'
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "hello world"
    else:
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
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
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
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
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

    with start_transaction(name="openai tx"):
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
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

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
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

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
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

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
        trace_lifecycle="stream",
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
    ), start_transaction(name="openai tx"):
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
    assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
    ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

    assert isinstance(ttft, float)
    assert ttft > 0

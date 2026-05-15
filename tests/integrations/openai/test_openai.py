import json

import pytest

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
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as DeltaChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.create_embedding_response import Usage as EmbeddingTokenUsage

SKIP_RESPONSES_TESTS = False

try:
    from openai.types.responses import (
        Response,
        ResponseOutputMessage,
        ResponseOutputText,
        ResponseUsage,
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
            ),
            output_tokens=10,
            output_tokens_details=OutputTokensDetails(
                reasoning_tokens=8,
            ),
            total_tokens=30,
        ),
    )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
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
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["data"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "get_messages",
    [
        pytest.param(
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
            id="blocks",
        ),
        pytest.param(
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
            id="parts",
        ),
        pytest.param(
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
            id="iterator",
        ),
    ],
)
def test_nonstreaming_chat_completion(
    sentry_init,
    capture_events,
    capture_items,
    get_messages,
    request,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

        assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert (
            "Message demonstrating the absence of truncation."
            in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert "the model response" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
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
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["data"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_messages",
    [
        pytest.param(
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
            id="blocks",
        ),
        pytest.param(
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
            id="parts",
        ),
        pytest.param(
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
            id="iterator",
        ),
    ],
)
async def test_nonstreaming_chat_completion_async(
    sentry_init,
    capture_events,
    capture_items,
    get_messages,
    request,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

        assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert (
            "Message demonstrating the absence of truncation."
            in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert "the model response" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


def tiktoken_encoding_if_installed():
    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        return "cl100k_base"
    except ImportError:
        return None


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
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
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["data"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

        try:
            import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

            assert span["data"]["gen_ai.usage.output_tokens"] == 2
            assert span["data"]["gen_ai.usage.input_tokens"] == 7
            assert span["data"]["gen_ai.usage.total_tokens"] == 9
        except ImportError:
            pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
def test_streaming_chat_completion_with_usage_in_stream(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """When stream_options=include_usage is set, token usage comes from the final chunk's usage field."""
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=False,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

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

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
def test_streaming_chat_completion_empty_content_preserves_token_usage(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """Token usage from the stream is recorded even when no content is produced (e.g. content filter)."""
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=False,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert "gen_ai.usage.output_tokens" not in span["attributes"]
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 20
    else:
        events = capture_events()

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

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert "gen_ai.usage.output_tokens" not in span["data"]
        assert span["data"]["gen_ai.usage.total_tokens"] == 20


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
@pytest.mark.asyncio
async def test_streaming_chat_completion_empty_content_preserves_token_usage_async(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """Token usage from the stream is recorded even when no content is produced - async variant."""
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=False,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert "gen_ai.usage.output_tokens" not in span["attributes"]
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 20
    else:
        events = capture_events()

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

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert "gen_ai.usage.output_tokens" not in span["data"]
        assert span["data"]["gen_ai.usage.total_tokens"] == 20


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the stream_options parameter.",
)
@pytest.mark.asyncio
async def test_streaming_chat_completion_async_with_usage_in_stream(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """When stream_options=include_usage is set, token usage comes from the final chunk's usage field (async)."""
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=False,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

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

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "get_messages",
    [
        pytest.param(
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
            id="blocks",
        ),
        pytest.param(
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
            id="parts",
        ),
        pytest.param(
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
            id="iterator",
        ),
    ],
)
def test_streaming_chat_completion(
    sentry_init,
    capture_events,
    capture_items,
    get_messages,
    request,
    get_model_response,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=True,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

        assert (
            "Message demonstrating the absence of truncation."
            in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "hello world" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        try:
            import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

            if "blocks" in param_id:
                assert span["attributes"]["gen_ai.usage.output_tokens"] == 2
                assert span["attributes"]["gen_ai.usage.input_tokens"] == 15
                assert span["attributes"]["gen_ai.usage.total_tokens"] == 17
            else:
                assert span["attributes"]["gen_ai.usage.output_tokens"] == 2
                assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
                assert span["attributes"]["gen_ai.usage.total_tokens"] == 22

        except ImportError:
            pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "hello world" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        try:
            import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

            if "blocks" in param_id:
                assert span["data"]["gen_ai.usage.output_tokens"] == 2
                assert span["data"]["gen_ai.usage.input_tokens"] == 15
                assert span["data"]["gen_ai.usage.total_tokens"] == 17
            else:
                assert span["data"]["gen_ai.usage.output_tokens"] == 2
                assert span["data"]["gen_ai.usage.input_tokens"] == 20
                assert span["data"]["gen_ai.usage.total_tokens"] == 22

        except ImportError:
            pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
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
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["data"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

        try:
            import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

            assert span["data"]["gen_ai.usage.output_tokens"] == 2
            assert span["data"]["gen_ai.usage.input_tokens"] == 7
            assert span["data"]["gen_ai.usage.total_tokens"] == 9

        except ImportError:
            pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_messages",
    [
        pytest.param(
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
            id="blocks",
        ),
        pytest.param(
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
            id="parts",
        ),
        pytest.param(
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
            id="iterator",
        ),
    ],
)
async def test_streaming_chat_completion_async(
    sentry_init,
    capture_events,
    capture_items,
    get_messages,
    request,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=True,
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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
        span = next(item.payload for item in items if item.type == "span")
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

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(
                span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            ) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

        assert (
            "Message demonstrating the absence of truncation."
            in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert "hello" in span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "hello world" in span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        try:
            import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

            if "blocks" in param_id:
                assert span["attributes"]["gen_ai.usage.output_tokens"] == 2
                assert span["attributes"]["gen_ai.usage.input_tokens"] == 15
                assert span["attributes"]["gen_ai.usage.total_tokens"] == 17
            else:
                assert span["attributes"]["gen_ai.usage.output_tokens"] == 2
                assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
                assert span["attributes"]["gen_ai.usage.total_tokens"] == 22

        except ImportError:
            pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly
    else:
        events = capture_events()

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
        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "model-id"

        param_id = request.node.callspec.id
        if "blocks" in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                }
            ]
        else:
            assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ]

            assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            assert "hello world" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

            try:
                import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

                if "blocks" in param_id:
                    assert span["data"]["gen_ai.usage.output_tokens"] == 2
                    assert span["data"]["gen_ai.usage.input_tokens"] == 15
                    assert span["data"]["gen_ai.usage.total_tokens"] == 17
                else:
                    assert span["data"]["gen_ai.usage.output_tokens"] == 2
                    assert span["data"]["gen_ai.usage.input_tokens"] == 20
                    assert span["data"]["gen_ai.usage.total_tokens"] == 22

            except ImportError:
                pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_bad_chat_completion(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    if stream_gen_ai_spans:
        items = capture_items("event")

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
    else:
        events = capture_events()

        client = OpenAI(api_key="z")
        client.chat.completions._post = mock.Mock(
            side_effect=OpenAIError("API rate limit reached")
        )
        with pytest.raises(OpenAIError):
            client.chat.completions.create(
                model="some-model",
                messages=[{"role": "system", "content": "hello"}],
            )

        (event,) = events

    assert event["level"] == "error"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_span_status_error(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    if stream_gen_ai_spans:
        items = capture_items("event", "transaction", "span")

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

        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["status"] == "error"
    else:
        events = capture_events()

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

        (error, transaction) = events
        assert error["level"] == "error"
        assert transaction["spans"][0]["status"] == "internal_error"
        assert transaction["spans"][0]["tags"]["status"] == "internal_error"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_bad_chat_completion_async(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )
    if stream_gen_ai_spans:
        items = capture_items("event")

        with pytest.raises(OpenAIError):
            await client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

        (event,) = (item.payload for item in items if item.type == "event")
    else:
        events = capture_events()

        with pytest.raises(OpenAIError):
            await client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

        (event,) = events

    assert event["level"] == "error"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            response = client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "text-embedding-3-large"
        )

        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["attributes"]

        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            response = client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.embeddings"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["data"]

        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "get_input",
    [
        pytest.param(
            lambda: "hello",
            id="string",
        ),
        pytest.param(
            lambda: ["First text", "Second text", "Third text"],
            id="string_sequence",
        ),
        pytest.param(
            lambda: iter(["First text", "Second text", "Third text"]),
            id="string_iterable",
        ),
        pytest.param(
            lambda: [5, 8, 13, 21, 34],
            id="tokens",
        ),
        pytest.param(
            lambda: iter(
                [5, 8, 13, 21, 34],
            ),
            id="token_iterable",
        ),
        pytest.param(
            lambda: [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
            id="tokens_sequence",
        ),
        pytest.param(
            lambda: iter(
                [
                    [5, 8, 13, 21, 34],
                    [8, 13, 21, 34, 55],
                ]
            ),
            id="tokens_sequence_iterable",
        ),
    ],
)
def test_embeddings_create(
    sentry_init,
    capture_events,
    capture_items,
    get_input,
    request,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            response = client.embeddings.create(
                input=get_input(), model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "text-embedding-3-large"
        )

        param_id = request.node.callspec.id
        if (
            "string" in param_id
            and "string_sequence" not in param_id
            and "string_iterable" not in param_id
        ):
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "hello"
            ]
        elif "string_sequence" in param_id or "string_iterable" in param_id:
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "First text",
                "Second text",
                "Third text",
            ]
        elif (
            "tokens" in param_id or "token_iterable" in param_id
        ) and "tokens_sequence" not in param_id:
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                5,
                8,
                13,
                21,
                34,
            ]
        else:
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ]

        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            response = client.embeddings.create(
                input=get_input(), model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.embeddings"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

        param_id = request.node.callspec.id
        if (
            "string" in param_id
            and "string_sequence" not in param_id
            and "string_iterable" not in param_id
        ):
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "hello"
            ]
        elif "string_sequence" in param_id or "string_iterable" in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "First text",
                "Second text",
                "Third text",
            ]
        elif (
            "tokens" in param_id or "token_iterable" in param_id
        ) and "tokens_sequence" not in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                5,
                8,
                13,
                21,
                34,
            ]
        else:
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ]

        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            response = await client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "text-embedding-3-large"
        )

        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["attributes"]

        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            response = await client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.embeddings"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["data"]

        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_input",
    [
        pytest.param(
            lambda: "hello",
            id="string",
        ),
        pytest.param(
            lambda: ["First text", "Second text", "Third text"],
            id="string_sequence",
        ),
        pytest.param(
            lambda: iter(["First text", "Second text", "Third text"]),
            id="string_iterable",
        ),
        pytest.param(
            lambda: [5, 8, 13, 21, 34],
            id="tokens",
        ),
        pytest.param(
            lambda: iter(
                [5, 8, 13, 21, 34],
            ),
            id="token_iterable",
        ),
        pytest.param(
            lambda: [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ],
            id="tokens_sequence",
        ),
        pytest.param(
            lambda: iter(
                [
                    [5, 8, 13, 21, 34],
                    [8, 13, 21, 34, 55],
                ]
            ),
            id="tokens_sequence_iterable",
        ),
    ],
)
async def test_embeddings_create_async(
    sentry_init,
    capture_events,
    capture_items,
    get_input,
    request,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            response = await client.embeddings.create(
                input=get_input(), model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.embeddings"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "text-embedding-3-large"
        )

        param_id = request.node.callspec.id
        if (
            "string" in param_id
            and "string_sequence" not in param_id
            and "string_iterable" not in param_id
        ):
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "hello"
            ]
        elif "string_sequence" in param_id or "string_iterable" in param_id:
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "First text",
                "Second text",
                "Third text",
            ]
        elif (
            "tokens" in param_id or "token_iterable" in param_id
        ) and "tokens_sequence" not in param_id:
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                5,
                8,
                13,
                21,
                34,
            ]
        else:
            assert json.loads(span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ]

        assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
        assert span["attributes"]["gen_ai.usage.total_tokens"] == 30
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            response = await client.embeddings.create(
                input=get_input(), model="text-embedding-3-large"
            )

        assert len(response.data[0].embedding) == 3

        tx = events[0]
        assert tx["type"] == "transaction"
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.embeddings"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-3-large"

        param_id = request.node.callspec.id
        if (
            "string" in param_id
            and "string_sequence" not in param_id
            and "string_iterable" not in param_id
        ):
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "hello"
            ]
        elif "string_sequence" in param_id or "string_iterable" in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                "First text",
                "Second text",
                "Third text",
            ]
        elif (
            "tokens" in param_id or "token_iterable" in param_id
        ) and "tokens_sequence" not in param_id:
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                5,
                8,
                13,
                21,
                34,
            ]
        else:
            assert json.loads(span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]) == [
                [5, 8, 13, 21, 34],
                [8, 13, 21, 34, 55],
            ]

        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_embeddings_create_raises_error(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = OpenAI(api_key="z")

    client.embeddings._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )

    if stream_gen_ai_spans:
        items = capture_items("event")

        with pytest.raises(OpenAIError):
            client.embeddings.create(input="hello", model="text-embedding-3-large")

        (event,) = (item.payload for item in items if item.type == "event")
    else:
        events = capture_events()

        with pytest.raises(OpenAIError):
            client.embeddings.create(input="hello", model="text-embedding-3-large")

        (event,) = events

    assert event["level"] == "error"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_embeddings_create_raises_error_async(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = AsyncOpenAI(api_key="z")

    client.embeddings._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )

    if stream_gen_ai_spans:
        items = capture_items("event")

        with pytest.raises(OpenAIError):
            await client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        (event,) = (item.payload for item in items if item.type == "event")
    else:
        events = capture_events()

        with pytest.raises(OpenAIError):
            await client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        (event,) = events

    assert event["level"] == "error"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_span_origin_nonstreaming_chat(
    sentry_init,
    capture_events,
    capture_items,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with start_transaction(name="openai tx"):
            client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

        (event,) = (item.payload for item in items if item.type == "transaction")
        assert event["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_span_origin_nonstreaming_chat_async(
    sentry_init,
    capture_events,
    capture_items,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with start_transaction(name="openai tx"):
            await client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

        (event,) = (item.payload for item in items if item.type == "transaction")
        assert event["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            await client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_span_origin_streaming_chat(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        client.chat.completions._post = mock.Mock(return_value=returned_stream)
        with start_transaction(name="openai tx"):
            response_stream = client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

            "".join(map(lambda x: x.choices[0].delta.content, response_stream))

        (event,) = (item.payload for item in items if item.type == "transaction")
        assert event["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"
    else:
        events = capture_events()

        client.chat.completions._post = mock.Mock(return_value=returned_stream)
        with start_transaction(name="openai tx"):
            response_stream = client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )

            "".join(map(lambda x: x.choices[0].delta.content, response_stream))

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_span_origin_streaming_chat_async(
    sentry_init,
    capture_events,
    capture_items,
    async_iterator,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with start_transaction(name="openai tx"):
            response_stream = await client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )
            async for _ in response_stream:
                pass

            # "".join(map(lambda x: x.choices[0].delta.content, response_stream))

        (event,) = (item.payload for item in items if item.type == "transaction")
        assert event["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            response_stream = await client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )
            async for _ in response_stream:
                pass

            # "".join(map(lambda x: x.choices[0].delta.content, response_stream))

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_span_origin_embeddings(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with start_transaction(name="openai tx"):
            client.embeddings.create(input="hello", model="text-embedding-3-large")

        (event,) = [item.payload for item in items if item.type == "transaction"]
        assert event["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            client.embeddings.create(input="hello", model="text-embedding-3-large")

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_span_origin_embeddings_async(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with start_transaction(name="openai tx"):
            await client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        (event,) = [item.payload for item in items if item.type == "transaction"]
        assert event["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.openai"
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            await client.embeddings.create(
                input="hello", model="text-embedding-3-large"
            )

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.ai.openai"


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


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_api_no_pii(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            client.responses.create(
                model="gpt-4o",
                instructions="You are a coding assistant that talks like a pirate.",
                input="How do I check if a Python object is an instance of a class?",
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )

        spans = [item.payload for item in items if item.type == "span"]

        assert len(spans) == 1
        assert spans[0]["attributes"] == {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.response.model": "response-model-id",
            "gen_ai.response.streaming": False,
            "gen_ai.system": "openai",
            "gen_ai.usage.input_tokens": 20,
            "gen_ai.usage.input_tokens.cached": 5,
            "gen_ai.usage.output_tokens": 10,
            "gen_ai.usage.output_tokens.reasoning": 8,
            "gen_ai.usage.total_tokens": 30,
            "sentry.environment": "production",
            "sentry.op": "gen_ai.responses",
            "sentry.origin": "auto.ai.openai",
            "sentry.release": mock.ANY,
            "sentry.sdk.name": "sentry.python",
            "sentry.sdk.version": mock.ANY,
            "sentry.segment.id": mock.ANY,
            "sentry.segment.name": "openai tx",
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        assert "gen_ai.system_instructions" not in spans[0]["attributes"]
        assert "gen_ai.request.messages" not in spans[0]["attributes"]
        assert "gen_ai.response.text" not in spans[0]["attributes"]
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            client.responses.create(
                model="gpt-4o",
                instructions="You are a coding assistant that talks like a pirate.",
                input="How do I check if a Python object is an instance of a class?",
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )

        (transaction,) = events
        spans = transaction["spans"]

        assert len(spans) == 1
        assert spans[0]["op"] == "gen_ai.responses"
        assert spans[0]["origin"] == "auto.ai.openai"
        assert spans[0]["data"] == {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.response.model": "response-model-id",
            "gen_ai.response.streaming": False,
            "gen_ai.system": "openai",
            "gen_ai.usage.input_tokens": 20,
            "gen_ai.usage.input_tokens.cached": 5,
            "gen_ai.usage.output_tokens": 10,
            "gen_ai.usage.output_tokens.reasoning": 8,
            "gen_ai.usage.total_tokens": 30,
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        assert "gen_ai.system_instructions" not in spans[0]["data"]
        assert "gen_ai.request.messages" not in spans[0]["data"]
        assert "gen_ai.response.text" not in spans[0]["data"]


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "instructions",
    (
        omit,
        None,
        "You are a coding assistant that talks like a pirate.",
    ),
)
@pytest.mark.parametrize(
    "input",
    [
        pytest.param(
            "How do I check if a Python object is an instance of a class?", id="string"
        ),
        pytest.param(
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            id="parts",
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_api(
    sentry_init,
    capture_events,
    capture_items,
    instructions,
    input,
    request,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            client.responses.create(
                model="gpt-4o",
                instructions=instructions,
                input=input,
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )

        spans = [item.payload for item in items if item.type == "span"]

        assert len(spans) == 1

        expected_data = {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
            "gen_ai.system": "openai",
            "gen_ai.response.model": "response-model-id",
            "gen_ai.response.streaming": False,
            "gen_ai.usage.input_tokens": 20,
            "gen_ai.usage.input_tokens.cached": 5,
            "gen_ai.usage.output_tokens": 10,
            "gen_ai.usage.output_tokens.reasoning": 8,
            "gen_ai.usage.total_tokens": 30,
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.response.text": "the model response",
            "sentry.environment": "production",
            "sentry.op": "gen_ai.responses",
            "sentry.origin": "auto.ai.openai",
            "sentry.release": mock.ANY,
            "sentry.sdk.name": "sentry.python",
            "sentry.sdk.version": mock.ANY,
            "sentry.segment.id": mock.ANY,
            "sentry.segment.name": "openai tx",
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        param_id = request.node.callspec.id
        if "string" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "string" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            }
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "blocks_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "parts_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "parts_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif instructions is None or isinstance(instructions, Omit):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        else:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )

        assert spans[0]["attributes"] == expected_data
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            client.responses.create(
                model="gpt-4o",
                instructions=instructions,
                input=input,
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )

        (transaction,) = events
        spans = transaction["spans"]

        assert len(spans) == 1
        assert spans[0]["op"] == "gen_ai.responses"
        assert spans[0]["origin"] == "auto.ai.openai"

        expected_data = {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
            "gen_ai.system": "openai",
            "gen_ai.response.model": "response-model-id",
            "gen_ai.response.streaming": False,
            "gen_ai.usage.input_tokens": 20,
            "gen_ai.usage.input_tokens.cached": 5,
            "gen_ai.usage.output_tokens": 10,
            "gen_ai.usage.output_tokens.reasoning": 8,
            "gen_ai.usage.total_tokens": 30,
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.response.text": "the model response",
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        param_id = request.node.callspec.id
        if "string" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "string" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            }
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "blocks_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "parts_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "parts_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif instructions is None or isinstance(instructions, Omit):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        else:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )

        assert spans[0]["data"] == expected_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    conversation,
    expected_id,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            client.responses.create(
                model="gpt-4o",
                input="hello",
                conversation=conversation,
            )

        (span,) = (item.payload for item in items if item.type == "span")

        if expected_id is None:
            assert "gen_ai.conversation.id" not in span["attributes"]
        else:
            assert span["attributes"]["gen_ai.conversation.id"] == expected_id
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            client.responses.create(
                model="gpt-4o",
                input="hello",
                conversation=conversation,
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        if expected_id is None:
            assert "gen_ai.conversation.id" not in span["data"]
        else:
            assert span["data"]["gen_ai.conversation.id"] == expected_id


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_error_in_responses_api(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )

    if stream_gen_ai_spans:
        items = capture_items("event", "transaction", "span")

        with start_transaction(name="openai tx"), pytest.raises(OpenAIError):
            client.responses.create(
                model="gpt-4o",
                instructions="You are a coding assistant that talks like a pirate.",
                input="How do I check if a Python object is an instance of a class?",
            )

        # make sure the span where the error occurred is captured
        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.op"] == "gen_ai.responses"

        (error_event,) = (item.payload for item in items if item.type == "event")

        assert error_event["level"] == "error"
        assert error_event["exception"]["values"][0]["type"] == "OpenAIError"

        (transaction_event,) = (
            item.payload for item in items if item.type == "transaction"
        )
    else:
        events = capture_events()

        with start_transaction(name="openai tx"), pytest.raises(OpenAIError):
            client.responses.create(
                model="gpt-4o",
                instructions="You are a coding assistant that talks like a pirate.",
                input="How do I check if a Python object is an instance of a class?",
            )

        (error_event, transaction_event) = events

        assert transaction_event["type"] == "transaction"
        # make sure the span where the error occurred is captured
        assert transaction_event["spans"][0]["op"] == "gen_ai.responses"

        assert error_event["level"] == "error"
        assert error_event["exception"]["values"][0]["type"] == "OpenAIError"

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
@pytest.mark.parametrize(
    "instructions",
    (
        omit,
        None,
        "You are a coding assistant that talks like a pirate.",
    ),
)
@pytest.mark.parametrize(
    "input",
    [
        pytest.param(
            "How do I check if a Python object is an instance of a class?", id="string"
        ),
        pytest.param(
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            id="parts",
        ),
    ],
)
async def test_ai_client_span_responses_async_api(
    sentry_init,
    capture_events,
    capture_items,
    instructions,
    input,
    request,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(return_value=EXAMPLE_RESPONSE)

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            await client.responses.create(
                model="gpt-4o",
                instructions=instructions,
                input=input,
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )

        spans = [item.payload for item in items if item.type == "span"]

        assert len(spans) == 1

        expected_data = {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
            "gen_ai.request.messages": '["How do I check if a Python object is an instance of a class?"]',
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
            "sentry.environment": "production",
            "sentry.op": "gen_ai.responses",
            "sentry.origin": "auto.ai.openai",
            "sentry.release": mock.ANY,
            "sentry.sdk.name": "sentry.python",
            "sentry.sdk.version": mock.ANY,
            "sentry.segment.id": mock.ANY,
            "sentry.segment.name": "openai tx",
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        param_id = request.node.callspec.id
        if "string" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "string" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            }
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "blocks_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "parts_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "parts_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif instructions is None or isinstance(instructions, Omit):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        else:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )

        assert spans[0]["attributes"] == expected_data
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            await client.responses.create(
                model="gpt-4o",
                instructions=instructions,
                input=input,
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )

        (transaction,) = events
        spans = transaction["spans"]

        assert len(spans) == 1
        assert spans[0]["op"] == "gen_ai.responses"
        assert spans[0]["origin"] == "auto.ai.openai"

        expected_data = {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
            "gen_ai.request.messages": '["How do I check if a Python object is an instance of a class?"]',
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
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        param_id = request.node.callspec.id
        if "string" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "string" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            }
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "blocks_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "parts_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "parts_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif instructions is None or isinstance(instructions, Omit):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        else:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )

        assert spans[0]["data"] == expected_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instructions",
    (
        omit,
        None,
        "You are a coding assistant that talks like a pirate.",
    ),
)
@pytest.mark.parametrize(
    "input",
    [
        pytest.param(
            "How do I check if a Python object is an instance of a class?", id="string"
        ),
        pytest.param(
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"role": "user", "content": "hello"},
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
                    "content": "Message demonstrating the absence of truncation.",
                },
                {"type": "message", "role": "user", "content": "hello"},
            ],
            id="parts",
        ),
    ],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_ai_client_span_streaming_responses_async_api(
    sentry_init,
    capture_events,
    capture_items,
    instructions,
    input,
    request,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )

    if stream_gen_ai_spans:
        items = capture_items("span")

        with mock.patch.object(
            client.responses._client._client,
            "send",
            return_value=returned_stream,
        ), start_transaction(name="openai tx"):
            result = await client.responses.create(
                model="gpt-4o",
                instructions=instructions,
                input=input,
                stream=True,
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )
            async for _ in result:
                pass

        spans = [item.payload for item in items if item.type == "span"]
        spans = [
            span
            for span in spans
            if span["attributes"]["sentry.op"] == OP.GEN_AI_RESPONSES
        ]

        assert len(spans) == 1

        expected_data = {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
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
            "sentry.release": mock.ANY,
            "sentry.sdk.name": "sentry.python",
            "sentry.sdk.version": mock.ANY,
            "sentry.segment.id": mock.ANY,
            "sentry.segment.name": "openai tx",
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        param_id = request.node.callspec.id
        if "string" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "string" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            }
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "blocks_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "blocks" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "parts_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif "parts_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        elif instructions is None or isinstance(instructions, Omit):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )
        else:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [
                            {
                                "type": "message",
                                "role": "user",
                                "content": "Message demonstrating the absence of truncation.",
                            },
                            {"type": "message", "role": "user", "content": "hello"},
                        ]
                    ),
                }
            )

        assert spans[0]["attributes"] == expected_data
    else:
        events = capture_events()

        with mock.patch.object(
            client.responses._client._client,
            "send",
            return_value=returned_stream,
        ), start_transaction(name="openai tx"):
            result = await client.responses.create(
                model="gpt-4o",
                instructions=instructions,
                input=input,
                stream=True,
                max_output_tokens=100,
                temperature=0.7,
                top_p=0.9,
            )
            async for _ in result:
                pass

        (transaction,) = events
        spans = [
            span for span in transaction["spans"] if span["op"] == OP.GEN_AI_RESPONSES
        ]

        assert len(spans) == 1
        assert spans[0]["origin"] == "auto.ai.openai"

        expected_data = {
            "gen_ai.operation.name": "responses",
            "gen_ai.request.max_tokens": 100,
            "gen_ai.request.temperature": 0.7,
            "gen_ai.request.top_p": 0.9,
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
            "thread.id": mock.ANY,
            "thread.name": mock.ANY,
        }

        param_id = request.node.callspec.id
        if "string" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "string" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            }
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        ["How do I check if a Python object is an instance of a class?"]
                    ),
                }
            )
        elif "blocks_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [{"type": "text", "content": "You are a helpful assistant."}]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "blocks" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "parts_no_type" in param_id and (
            instructions is None or isinstance(instructions, Omit)
        ):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif "parts_no_type" in param_id:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"role": "user", "content": "hello"}]
                    ),
                }
            )
        elif instructions is None or isinstance(instructions, Omit):  # type: ignore
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )
        else:
            expected_data.update(
                {
                    "gen_ai.system_instructions": safe_serialize(
                        [
                            {
                                "type": "text",
                                "content": "You are a coding assistant that talks like a pirate.",
                            },
                            {"type": "text", "content": "You are a helpful assistant."},
                            {"type": "text", "content": "Be concise and clear."},
                        ]
                    ),
                    "gen_ai.request.messages": safe_serialize(
                        [{"type": "message", "role": "user", "content": "hello"}]
                    ),
                }
            )

        assert spans[0]["data"] == expected_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_error_in_responses_async_api(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = AsyncOpenAI(api_key="z")
    client.responses._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )

    if stream_gen_ai_spans:
        items = capture_items("event", "transaction", "span")

        with start_transaction(name="openai tx"), pytest.raises(OpenAIError):
            await client.responses.create(
                model="gpt-4o",
                instructions="You are a coding assistant that talks like a pirate.",
                input="How do I check if a Python object is an instance of a class?",
            )

        # make sure the span where the error occurred is captured
        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.op"] == "gen_ai.responses"

        (error_event,) = (item.payload for item in items if item.type == "event")

        assert error_event["level"] == "error"
        assert error_event["exception"]["values"][0]["type"] == "OpenAIError"

        (transaction_event,) = (
            item.payload for item in items if item.type == "transaction"
        )
    else:
        events = capture_events()

        with start_transaction(name="openai tx"), pytest.raises(OpenAIError):
            await client.responses.create(
                model="gpt-4o",
                instructions="You are a coding assistant that talks like a pirate.",
                input="How do I check if a Python object is an instance of a class?",
            )

        (error_event, transaction_event) = events

        assert transaction_event["type"] == "transaction"
        # make sure the span where the error occurred is captured
        assert transaction_event["spans"][0]["op"] == "gen_ai.responses"

        assert error_event["level"] == "error"
        assert error_event["exception"]["values"][0]["type"] == "OpenAIError"

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
    )


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


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_streaming_responses_api(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(
            EXAMPLE_RESPONSES_STREAM,
        )
    )

    if stream_gen_ai_spans:
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
            )

            response_string = ""
            for item in response_stream:
                if hasattr(item, "delta"):
                    response_string += item.delta

        assert response_string == "hello world"

        (span,) = (item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.responses"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

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
    else:
        events = capture_events()

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
            )

            response_string = ""
            for item in response_stream:
                if hasattr(item, "delta"):
                    response_string += item.delta

        assert response_string == "hello world"

        (transaction,) = events
        (span,) = transaction["spans"]
        assert span["op"] == "gen_ai.responses"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

        if send_default_pii and include_prompts:
            assert span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES] == '["hello"]'
            assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "hello world"
        else:
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_streaming_responses_api_async(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            OpenAIIntegration(
                include_prompts=include_prompts,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )

    if stream_gen_ai_spans:
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
            )

            response_string = ""
            async for item in response_stream:
                if hasattr(item, "delta"):
                    response_string += item.delta

        assert response_string == "hello world"

        (span,) = (item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.responses"
        assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

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
    else:
        events = capture_events()

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
            )

            response_string = ""
            async for item in response_stream:
                if hasattr(item, "delta"):
                    response_string += item.delta

        assert response_string == "hello world"

        (transaction,) = events
        (span,) = transaction["spans"]
        assert span["op"] == "gen_ai.responses"
        assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "response-model-id"

        if send_default_pii and include_prompts:
            assert span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES] == '["hello"]'
            assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "hello world"
        else:
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

        assert span["data"]["gen_ai.usage.input_tokens"] == 20
        assert span["data"]["gen_ai.usage.output_tokens"] == 10
        assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(
    OPENAI_VERSION <= (1, 1, 0),
    reason="OpenAI versions <=1.1.0 do not support the tools parameter.",
)
@pytest.mark.parametrize(
    "tools",
    [[], None, NOT_GIVEN, omit],
)
def test_empty_tools_in_chat_completion(
    sentry_init,
    capture_events,
    capture_items,
    tools,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            client.chat.completions.create(
                model="some-model",
                messages=[{"role": "system", "content": "hello"}],
                tools=tools,
            )

        span = next(item.payload for item in items if item.type == "span")

        assert "gen_ai.request.available_tools" not in span["attributes"]
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            client.chat.completions.create(
                model="some-model",
                messages=[{"role": "system", "content": "hello"}],
                tools=tools,
            )

        (event,) = events
        span = event["spans"][0]

        assert "gen_ai.request.available_tools" not in span["data"]


# Test messages with mixed roles including "ai" that should be mapped to "assistant"
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
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
    capture_events,
    capture_items,
    test_message,
    expected_role,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    """Test that OpenAI integration properly maps message roles like 'ai' to 'assistant'"""

    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction(name="openai tx"):
            client.chat.completions.create(model="test-model", messages=test_messages)

        # Verify that the span was created correctly
        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]

        stored_messages = json.loads(
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
    else:
        events = capture_events()

        with start_transaction(name="openai tx"):
            client.chat.completions.create(model="test-model", messages=test_messages)

        # Verify that the span was created correctly
        (event,) = events
        span = event["spans"][0]
        assert span["op"] == "gen_ai.chat"
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]

        stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    assert len(stored_messages) == 1
    assert stored_messages[0]["role"] == expected_role


def test_openai_message_truncation(
    sentry_init,
    capture_events,
    nonstreaming_chat_completions_model_response,
):
    """Test that large messages are truncated properly in OpenAI integration."""
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
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

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )
    large_messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": large_content},
        {"role": "user", "content": large_content},
    ]

    events = capture_events()

    with start_transaction(name="openai tx"):
        client.chat.completions.create(
            model="some-model",
            messages=large_messages,
        )

    (event,) = events
    span = event["spans"][0]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]

    messages_data = span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) <= len(large_messages)

    meta_path = event["_meta"]
    span_meta = meta_path["spans"]["0"]["data"]
    messages_meta = span_meta[SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert "len" in messages_meta.get("", {})


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_streaming_chat_completion_ttft(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """
    Test that streaming chat completions capture time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
        ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]
    else:
        events = capture_events()

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

        (tx,) = events
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["data"]
        ttft = span["data"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

    assert isinstance(ttft, float)
    assert ttft > 0


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_streaming_chat_completion_ttft_async(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """
    Test that async streaming chat completions capture time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
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

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.chat"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
        ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]
    else:
        events = capture_events()

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

        (tx,) = events
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.chat"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["data"]
        ttft = span["data"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

    assert isinstance(ttft, float)
    assert ttft > 0


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
def test_streaming_responses_api_ttft(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """
    Test that streaming responses API captures time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = OpenAI(api_key="z")
    returned_stream = get_model_response(
        server_side_event_chunks(EXAMPLE_RESPONSES_STREAM)
    )

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.responses"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
        ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]
    else:
        events = capture_events()

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

        (tx,) = events
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.responses"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["data"]
        ttft = span["data"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

    assert isinstance(ttft, float)
    assert ttft > 0


# noinspection PyTypeChecker
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_RESPONSES_TESTS, reason="Responses API not available")
async def test_streaming_responses_api_ttft_async(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    stream_gen_ai_spans,
):
    """
    Test that async streaming responses API captures time-to-first-token (TTFT).
    """
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    client = AsyncOpenAI(api_key="z")
    returned_stream = get_model_response(
        async_iterator(server_side_event_chunks(EXAMPLE_RESPONSES_STREAM))
    )

    if stream_gen_ai_spans:
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

        span = next(item.payload for item in items if item.type == "span")
        assert span["attributes"]["sentry.op"] == "gen_ai.responses"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["attributes"]
        ttft = span["attributes"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]
    else:
        events = capture_events()

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

        (tx,) = events
        span = tx["spans"][0]
        assert span["op"] == "gen_ai.responses"

        # Verify TTFT is captured
        assert SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN in span["data"]
        ttft = span["data"][SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]

    assert isinstance(ttft, float)
    assert ttft > 0

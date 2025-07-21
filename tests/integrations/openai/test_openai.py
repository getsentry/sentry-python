import pytest
from openai import AsyncOpenAI, OpenAI, AsyncStream, Stream, OpenAIError
from openai.types import CompletionUsage, CreateEmbeddingResponse, Embedding
from openai.types.chat import ChatCompletion, ChatCompletionMessage, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import ChoiceDelta, Choice as DeltaChoice
from openai.types.create_embedding_response import Usage as EmbeddingTokenUsage

SKIP_API_TESTS = False

try:
    from openai.types.responses.response_usage import (
        InputTokensDetails,
        OutputTokensDetails,
    )
    from openai.types.responses import (
        Response,
        ResponseUsage,
        ResponseOutputMessage,
        ResponseOutputText,
    )
except ImportError:
    SKIP_API_TESTS = True

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.openai import (
    OpenAIIntegration,
    _calculate_token_usage,
)

from unittest import mock  # python 3.3 and above

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


EXAMPLE_CHAT_COMPLETION = ChatCompletion(
    id="chat-id",
    choices=[
        Choice(
            index=0,
            finish_reason="stop",
            message=ChatCompletionMessage(
                role="assistant", content="the model response"
            ),
        )
    ],
    created=10000000,
    model="model-id",
    object="chat.completion",
    usage=CompletionUsage(
        completion_tokens=10,
        prompt_tokens=20,
        total_tokens=30,
    ),
)


if SKIP_API_TESTS:
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
        model="model-id",
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


async def async_iterator(values):
    for value in values:
        yield value


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_nonstreaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(return_value=EXAMPLE_CHAT_COMPLETION)

    with start_transaction(name="openai tx"):
        response = (
            client.chat.completions.create(
                model="some-model", messages=[{"role": "system", "content": "hello"}]
            )
            .choices[0]
            .message.content
        )

    assert response == "the model response"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]["content"]
        assert (
            "the model response"
            in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]["content"]
        )
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"]["gen_ai.usage.output_tokens"] == 10
    assert span["data"]["gen_ai.usage.input_tokens"] == 20
    assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_nonstreaming_chat_completion_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = AsyncMock(return_value=EXAMPLE_CHAT_COMPLETION)

    with start_transaction(name="openai tx"):
        response = await client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )
        response = response.choices[0].message.content

    assert response == "the model response"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]["content"]
        assert (
            "the model response"
            in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]["content"]
        )
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

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
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_streaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts
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
    )
    events = capture_events()

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

    client.chat.completions._post = mock.Mock(return_value=returned_stream)
    with start_transaction(name="openai tx"):
        response_stream = client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )
        response_string = "".join(
            map(lambda x: x.choices[0].delta.content, response_stream)
        )
    assert response_string == "hello world"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]["content"]
        assert "hello world" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        assert span["data"]["gen_ai.usage.output_tokens"] == 2
        assert span["data"]["gen_ai.usage.input_tokens"] == 1
        assert span["data"]["gen_ai.usage.total_tokens"] == 3
    except ImportError:
        pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


# noinspection PyTypeChecker
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_streaming_chat_completion_async(
    sentry_init, capture_events, send_default_pii, include_prompts
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
    )
    events = capture_events()

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
    with start_transaction(name="openai tx"):
        response_stream = await client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

        response_string = ""
        async for x in response_stream:
            response_string += x.choices[0].delta.content

    assert response_string == "hello world"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]["content"]
        assert "hello world" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        assert span["data"]["gen_ai.usage.output_tokens"] == 2
        assert span["data"]["gen_ai.usage.input_tokens"] == 1
        assert span["data"]["gen_ai.usage.total_tokens"] == 3
    except ImportError:
        pass  # if tiktoken is not installed, we can't guarantee token usage will be calculated properly


def test_bad_chat_completion(sentry_init, capture_events):
    sentry_init(integrations=[OpenAIIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )
    with pytest.raises(OpenAIError):
        client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

    (event,) = events
    assert event["level"] == "error"


@pytest.mark.asyncio
async def test_bad_chat_completion_async(sentry_init, capture_events):
    sentry_init(integrations=[OpenAIIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )
    with pytest.raises(OpenAIError):
        await client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

    (event,) = events
    assert event["level"] == "error"


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_embeddings_create(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

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
    with start_transaction(name="openai tx"):
        response = client.embeddings.create(
            input="hello", model="text-embedding-3-large"
        )

    assert len(response.data[0].embedding) == 3

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.embeddings"
    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]

    assert span["data"]["gen_ai.usage.input_tokens"] == 20
    assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_embeddings_create_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

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
    with start_transaction(name="openai tx"):
        response = await client.embeddings.create(
            input="hello", model="text-embedding-3-large"
        )

    assert len(response.data[0].embedding) == 3

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.embeddings"
    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]

    assert span["data"]["gen_ai.usage.input_tokens"] == 20
    assert span["data"]["gen_ai.usage.total_tokens"] == 30


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_embeddings_create_raises_error(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = OpenAI(api_key="z")

    client.embeddings._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )

    with pytest.raises(OpenAIError):
        client.embeddings.create(input="hello", model="text-embedding-3-large")

    (event,) = events
    assert event["level"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_embeddings_create_raises_error_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = AsyncOpenAI(api_key="z")

    client.embeddings._post = AsyncMock(
        side_effect=OpenAIError("API rate limit reached")
    )

    with pytest.raises(OpenAIError):
        await client.embeddings.create(input="hello", model="text-embedding-3-large")

    (event,) = events
    assert event["level"] == "error"


def test_span_origin_nonstreaming_chat(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = OpenAI(api_key="z")
    client.chat.completions._post = mock.Mock(return_value=EXAMPLE_CHAT_COMPLETION)

    with start_transaction(name="openai tx"):
        client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.asyncio
async def test_span_origin_nonstreaming_chat_async(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = AsyncOpenAI(api_key="z")
    client.chat.completions._post = AsyncMock(return_value=EXAMPLE_CHAT_COMPLETION)

    with start_transaction(name="openai tx"):
        await client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.openai"


def test_span_origin_streaming_chat(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

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

    client.chat.completions._post = mock.Mock(return_value=returned_stream)
    with start_transaction(name="openai tx"):
        response_stream = client.chat.completions.create(
            model="some-model", messages=[{"role": "system", "content": "hello"}]
        )

        "".join(map(lambda x: x.choices[0].delta.content, response_stream))

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.asyncio
async def test_span_origin_streaming_chat_async(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

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


def test_span_origin_embeddings(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

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
    with start_transaction(name="openai tx"):
        client.embeddings.create(input="hello", model="text-embedding-3-large")

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.openai"


@pytest.mark.asyncio
async def test_span_origin_embeddings_async(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

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
    with start_transaction(name="openai tx"):
        await client.embeddings.create(input="hello", model="text-embedding-3-large")

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.openai"


def test_calculate_token_usage_a():
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
        _calculate_token_usage(
            messages, response, span, streaming_message_responses, count_tokens
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=None,
            output_tokens=10,
            output_tokens_reasoning=None,
            total_tokens=30,
        )


def test_calculate_token_usage_b():
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
        _calculate_token_usage(
            messages, response, span, streaming_message_responses, count_tokens
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=11,
            input_tokens_cached=None,
            output_tokens=10,
            output_tokens_reasoning=None,
            total_tokens=10,
        )


def test_calculate_token_usage_c():
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
        _calculate_token_usage(
            messages, response, span, streaming_message_responses, count_tokens
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=None,
            output_tokens=11,
            output_tokens_reasoning=None,
            total_tokens=20,
        )


def test_calculate_token_usage_d():
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    response.usage = mock.MagicMock()
    response.usage.prompt_tokens = 20
    response.usage.total_tokens = 20
    response.choices = [
        mock.MagicMock(message="one"),
        mock.MagicMock(message="two"),
        mock.MagicMock(message="three"),
    ]
    messages = []
    streaming_message_responses = []

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_token_usage(
            messages, response, span, streaming_message_responses, count_tokens
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=20,
            input_tokens_cached=None,
            output_tokens=None,
            output_tokens_reasoning=None,
            total_tokens=20,
        )


def test_calculate_token_usage_e():
    span = mock.MagicMock()

    def count_tokens(msg):
        return len(str(msg))

    response = mock.MagicMock()
    messages = []
    streaming_message_responses = None

    with mock.patch(
        "sentry_sdk.integrations.openai.record_token_usage"
    ) as mock_record_token_usage:
        _calculate_token_usage(
            messages, response, span, streaming_message_responses, count_tokens
        )
        mock_record_token_usage.assert_called_once_with(
            span,
            input_tokens=None,
            input_tokens_cached=None,
            output_tokens=None,
            output_tokens_reasoning=None,
            total_tokens=None,
        )


@pytest.mark.skipif(SKIP_API_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_api_no_pii(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="openai tx"):
        client.responses.create(
            model="gpt-4o",
            instructions="You are a coding assistant that talks like a pirate.",
            input="How do I check if a Python object is an instance of a class?",
        )

    (transaction,) = events
    spans = transaction["spans"]

    assert len(spans) == 1
    assert spans[0]["op"] == "gen_ai.responses"
    assert spans[0]["origin"] == "auto.ai.openai"
    assert spans[0]["data"] == {
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.input_tokens.cached": 5,
        "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.output_tokens.reasoning": 8,
        "gen_ai.usage.total_tokens": 30,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    assert "gen_ai.request.messages" not in spans[0]["data"]
    assert "gen_ai.response.text" not in spans[0]["data"]


@pytest.mark.skipif(SKIP_API_TESTS, reason="Responses API not available")
def test_ai_client_span_responses_api(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="openai tx"):
        client.responses.create(
            model="gpt-4o",
            instructions="You are a coding assistant that talks like a pirate.",
            input="How do I check if a Python object is an instance of a class?",
        )

    (transaction,) = events
    spans = transaction["spans"]

    assert len(spans) == 1
    assert spans[0]["op"] == "gen_ai.responses"
    assert spans[0]["origin"] == "auto.ai.openai"
    assert spans[0]["data"] == {
        "gen_ai.request.messages": "How do I check if a Python object is an instance of a class?",
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.input_tokens.cached": 5,
        "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.output_tokens.reasoning": 8,
        "gen_ai.usage.total_tokens": 30,
        "gen_ai.response.text": '[{"id": "message-id", "content": [{"annotations": [], "text": "the model response", "type": "output_text"}], "role": "assistant", "status": "completed", "type": "message"}]',
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }


@pytest.mark.skipif(SKIP_API_TESTS, reason="Responses API not available")
def test_error_in_responses_api(sentry_init, capture_events):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    client = OpenAI(api_key="z")
    client.responses._post = mock.Mock(
        side_effect=OpenAIError("API rate limit reached")
    )

    with start_transaction(name="openai tx"):
        with pytest.raises(OpenAIError):
            client.responses.create(
                model="gpt-4o",
                instructions="You are a coding assistant that talks like a pirate.",
                input="How do I check if a Python object is an instance of a class?",
            )

    (error_event, transaction_event) = events
    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "OpenAIError"
    assert transaction_event["type"] == "transaction"
    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
    )

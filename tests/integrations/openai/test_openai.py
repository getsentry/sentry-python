import pytest
from openai import OpenAI, Stream, OpenAIError
from openai.types import CompletionUsage, CreateEmbeddingResponse, Embedding
from openai.types.chat import ChatCompletion, ChatCompletionMessage, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import ChoiceDelta, Choice as DeltaChoice
from openai.types.create_embedding_response import Usage as EmbeddingTokenUsage

from sentry_sdk import start_transaction
from sentry_sdk.integrations.openai import (
    OpenAIIntegration,
    COMPLETION_TOKENS_USED,
    PROMPT_TOKENS_USED,
    TOTAL_TOKENS_USED,
)

from unittest import mock  # python 3.3 and above


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
    assert span["op"] == "ai.chat_completions.create.openai"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"]["ai.input_messages"][0]["content"]
        assert "the model response" in span["data"]["ai.responses"][0]["content"]
    else:
        assert "ai.input_messages" not in span["data"]
        assert "ai.responses" not in span["data"]

    assert span["data"][COMPLETION_TOKENS_USED] == 10
    assert span["data"][PROMPT_TOKENS_USED] == 20
    assert span["data"][TOTAL_TOKENS_USED] == 30


# noinspection PyTypeChecker
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_streaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=include_prompts)],
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
    assert span["op"] == "ai.chat_completions.create.openai"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"]["ai.input_messages"][0]["content"]
        assert "hello world" in span["data"]["ai.responses"][0]
    else:
        assert "ai.input_messages" not in span["data"]
        assert "ai.responses" not in span["data"]

    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        assert span["data"][COMPLETION_TOKENS_USED] == 2
        assert span["data"][PROMPT_TOKENS_USED] == 1
        assert span["data"][TOTAL_TOKENS_USED] == 3
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
    assert span["op"] == "ai.embeddings.create.openai"
    if send_default_pii and include_prompts:
        assert "hello" in span["data"]["ai.input_messages"][0]
    else:
        assert "ai.input_messages" not in span["data"]

    assert span["data"][PROMPT_TOKENS_USED] == 20
    assert span["data"][TOTAL_TOKENS_USED] == 30

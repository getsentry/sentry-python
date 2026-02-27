import json

import httpx
import pytest
from unittest import mock

from httpx import Client as HTTPXClient

from cohere import Client, ChatMessage

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.cohere import CohereIntegration

try:
    from cohere import ClientV2

    has_v2 = True
except ImportError:
    has_v2 = False


# --- V1 Chat (non-streaming) ---


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_v1_nonstreaming_chat(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "text": "the model response",
                "generation_id": "gen-123",
                "finish_reason": "COMPLETE",
                "meta": {
                    "billed_units": {
                        "output_tokens": 10,
                        "input_tokens": 20,
                    }
                },
            },
        )
    )

    with start_transaction(name="cohere tx"):
        response = client.chat(
            model="some-model",
            chat_history=[ChatMessage(role="SYSTEM", message="some context")],
            message="hello",
        )

    assert response.text == "the model response"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"
    assert span["origin"] == "auto.ai.cohere"
    assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "cohere"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"]["gen_ai.usage.output_tokens"] == 10
    assert span["data"]["gen_ai.usage.input_tokens"] == 20
    assert span["data"]["gen_ai.usage.total_tokens"] == 30


# --- V1 Chat (streaming) ---


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_v1_streaming_chat(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = Client(api_key="z")
    HTTPXClient.send = mock.Mock(
        return_value=httpx.Response(
            200,
            content="\n".join(
                [
                    json.dumps({"event_type": "text-generation", "text": "the model "}),
                    json.dumps({"event_type": "text-generation", "text": "response"}),
                    json.dumps(
                        {
                            "event_type": "stream-end",
                            "finish_reason": "COMPLETE",
                            "response": {
                                "text": "the model response",
                                "generation_id": "gen-123",
                                "meta": {
                                    "billed_units": {
                                        "output_tokens": 10,
                                        "input_tokens": 20,
                                    }
                                },
                            },
                        }
                    ),
                ]
            ),
        )
    )

    with start_transaction(name="cohere tx"):
        responses = list(
            client.chat_stream(
                model="some-model",
                chat_history=[ChatMessage(role="SYSTEM", message="some context")],
                message="hello",
            )
        )
        response_string = responses[-1].response.text

    assert response_string == "the model response"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"
    assert span["origin"] == "auto.ai.cohere"
    assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "cohere"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "some-model"
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"]["gen_ai.usage.output_tokens"] == 10
    assert span["data"]["gen_ai.usage.input_tokens"] == 20
    assert span["data"]["gen_ai.usage.total_tokens"] == 30


# --- V1 Error ---


def test_v1_bad_chat(sentry_init, capture_events):
    sentry_init(integrations=[CohereIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        side_effect=httpx.HTTPError("API rate limit reached")
    )
    with pytest.raises(httpx.HTTPError):
        client.chat(model="some-model", message="hello")

    (event,) = events
    assert event["level"] == "error"


def test_v1_span_status_error(sentry_init, capture_events):
    sentry_init(integrations=[CohereIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="test"):
        client = Client(api_key="z")
        HTTPXClient.request = mock.Mock(
            side_effect=httpx.HTTPError("API rate limit reached")
        )
        with pytest.raises(httpx.HTTPError):
            client.chat(model="some-model", message="hello")

    (error, transaction) = events
    assert error["level"] == "error"
    assert transaction["spans"][0]["status"] == "internal_error"
    assert transaction["spans"][0]["tags"]["status"] == "internal_error"
    assert transaction["contexts"]["trace"]["status"] == "internal_error"


# --- V1 Embed ---


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_v1_embed(sentry_init, capture_events, send_default_pii, include_prompts):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "response_type": "embeddings_floats",
                "id": "1",
                "texts": ["hello"],
                "embeddings": [[1.0, 2.0, 3.0]],
                "meta": {
                    "billed_units": {
                        "input_tokens": 10,
                    }
                },
            },
        )
    )

    with start_transaction(name="cohere tx"):
        response = client.embed(texts=["hello"], model="embed-english-v3.0")

    assert len(response.embeddings[0]) == 3

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.embeddings"
    assert span["origin"] == "auto.ai.cohere"
    assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "cohere"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
    else:
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["data"]

    assert span["data"]["gen_ai.usage.input_tokens"] == 10
    assert span["data"]["gen_ai.usage.total_tokens"] == 10


# --- V2 Chat (non-streaming) ---


@pytest.mark.skipif(not has_v2, reason="Cohere V2 client not available")
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_v2_nonstreaming_chat(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = ClientV2(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-123",
                "finish_reason": "COMPLETE",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "the model response"}],
                },
                "usage": {
                    "billed_units": {
                        "input_tokens": 20,
                        "output_tokens": 10,
                    },
                    "tokens": {
                        "input_tokens": 25,
                        "output_tokens": 15,
                    },
                },
            },
        )
    )

    with start_transaction(name="cohere tx"):
        response = client.chat(
            model="some-model",
            messages=[
                {"role": "system", "content": "some context"},
                {"role": "user", "content": "hello"},
            ],
        )

    assert response.message.content[0].text == "the model response"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"
    assert span["origin"] == "auto.ai.cohere"
    assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "cohere"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "some-model"
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_ID] == "resp-123"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"]["gen_ai.usage.output_tokens"] == 10
    assert span["data"]["gen_ai.usage.input_tokens"] == 20
    assert span["data"]["gen_ai.usage.total_tokens"] == 30


# --- V2 Chat (streaming) ---


@pytest.mark.skipif(not has_v2, reason="Cohere V2 client not available")
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_v2_streaming_chat(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = ClientV2(api_key="z")

    # SSE format: each event is "data: ...\n\n"
    sse_content = "".join(
        [
            'data: {"type":"message-start","id":"resp-123"}\n',
            "\n",
            'data: {"type":"content-delta","index":0,"delta":{"type":"content-delta","message":{"role":"assistant","content":{"type":"text","text":"the model "}}}}\n',
            "\n",
            'data: {"type":"content-delta","index":0,"delta":{"type":"content-delta","message":{"role":"assistant","content":{"type":"text","text":"response"}}}}\n',
            "\n",
            'data: {"type":"message-end","id":"resp-123","delta":{"finish_reason":"COMPLETE","usage":{"billed_units":{"input_tokens":20,"output_tokens":10},"tokens":{"input_tokens":25,"output_tokens":15}}}}\n',
            "\n",
        ]
    )

    HTTPXClient.send = mock.Mock(
        return_value=httpx.Response(
            200,
            content=sse_content,
            headers={"content-type": "text/event-stream"},
        )
    )

    with start_transaction(name="cohere tx"):
        responses = list(
            client.chat_stream(
                model="some-model",
                messages=[
                    {"role": "user", "content": "hello"},
                ],
            )
        )

    assert len(responses) > 0
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.chat"
    assert span["origin"] == "auto.ai.cohere"
    assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "cohere"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"]["gen_ai.usage.output_tokens"] == 10
    assert span["data"]["gen_ai.usage.input_tokens"] == 20
    assert span["data"]["gen_ai.usage.total_tokens"] == 30


# --- V2 Error ---


@pytest.mark.skipif(not has_v2, reason="Cohere V2 client not available")
def test_v2_bad_chat(sentry_init, capture_events):
    sentry_init(integrations=[CohereIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = ClientV2(api_key="z")
    HTTPXClient.request = mock.Mock(
        side_effect=httpx.HTTPError("API rate limit reached")
    )
    with pytest.raises(httpx.HTTPError):
        client.chat(
            model="some-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    (event,) = events
    assert event["level"] == "error"


# --- V2 Embed ---


@pytest.mark.skipif(not has_v2, reason="Cohere V2 client not available")
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_v2_embed(sentry_init, capture_events, send_default_pii, include_prompts):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = ClientV2(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "response_type": "embeddings_floats",
                "id": "1",
                "texts": ["hello"],
                "embeddings": {"float": [[1.0, 2.0, 3.0]]},
                "meta": {
                    "billed_units": {
                        "input_tokens": 10,
                    }
                },
            },
        )
    )

    with start_transaction(name="cohere tx"):
        client.embed(
            texts=["hello"],
            model="embed-english-v3.0",
            input_type="search_document",
            embedding_types=["float"],
        )

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "gen_ai.embeddings"
    assert span["origin"] == "auto.ai.cohere"
    assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "cohere"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
    else:
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["data"]

    assert span["data"]["gen_ai.usage.input_tokens"] == 10
    assert span["data"]["gen_ai.usage.total_tokens"] == 10

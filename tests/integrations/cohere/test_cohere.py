import json

import httpx
import pytest
from cohere import Client, ChatMessage

from sentry_sdk import start_span
from sentry_sdk.integrations.cohere import CohereIntegration

from unittest import mock  # python 3.3 and above
from httpx import Client as HTTPXClient


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_nonstreaming_chat(
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
                "meta": {
                    "billed_units": {
                        "output_tokens": 10,
                        "input_tokens": 20,
                    }
                },
            },
        )
    )

    with start_span(name="cohere tx"):
        response = client.chat(
            model="some-model",
            chat_history=[ChatMessage(role="SYSTEM", message="some context")],
            message="hello",
        ).text

    assert response == "the model response"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "ai.chat_completions.create.cohere"
    assert span["data"]["ai.model_id"] == "some-model"

    if send_default_pii and include_prompts:
        input_messages = json.loads(span["data"]["ai.input_messages"])
        assert "some context" in input_messages[0]["content"]
        assert "hello" in input_messages[1]["content"]
        assert "the model response" in span["data"]["ai.responses"]
    else:
        assert "ai.input_messages" not in span["data"]
        assert "ai.responses" not in span["data"]

    assert span["data"]["ai.completion_tokens_used"] == 10
    assert span["data"]["ai.prompt_tokens_used"] == 20
    assert span["data"]["ai.total_tokens_used"] == 30


# noinspection PyTypeChecker
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_streaming_chat(sentry_init, capture_events, send_default_pii, include_prompts):
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

    with start_span(name="cohere tx"):
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
    assert span["op"] == "ai.chat_completions.create.cohere"
    assert span["data"]["ai.model_id"] == "some-model"

    if send_default_pii and include_prompts:
        input_messages = json.loads(span["data"]["ai.input_messages"])
        assert "some context" in input_messages[0]["content"]
        assert "hello" in input_messages[1]["content"]
        assert "the model response" in span["data"]["ai.responses"]
    else:
        assert "ai.input_messages" not in span["data"]
        assert "ai.responses" not in span["data"]

    assert span["data"]["ai.completion_tokens_used"] == 10
    assert span["data"]["ai.prompt_tokens_used"] == 20
    assert span["data"]["ai.total_tokens_used"] == 30


def test_bad_chat(sentry_init, capture_events):
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


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_embed(sentry_init, capture_events, send_default_pii, include_prompts):
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

    with start_span(name="cohere tx"):
        response = client.embed(texts=["hello"], model="text-embedding-3-large")

    assert len(response.embeddings[0]) == 3

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "ai.embeddings.create.cohere"
    if send_default_pii and include_prompts:
        assert "hello" in span["data"]["ai.input_messages"]
    else:
        assert "ai.input_messages" not in span["data"]

    assert span["data"]["ai.prompt_tokens_used"] == 10
    assert span["data"]["ai.total_tokens_used"] == 10


def test_span_origin_chat(sentry_init, capture_events):
    sentry_init(
        integrations=[CohereIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "text": "the model response",
                "meta": {
                    "billed_units": {
                        "output_tokens": 10,
                        "input_tokens": 20,
                    }
                },
            },
        )
    )

    with start_span(name="cohere tx"):
        client.chat(
            model="some-model",
            chat_history=[ChatMessage(role="SYSTEM", message="some context")],
            message="hello",
        ).text

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.cohere"


def test_span_origin_embed(sentry_init, capture_events):
    sentry_init(
        integrations=[CohereIntegration()],
        traces_sample_rate=1.0,
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

    with start_span(name="cohere tx"):
        client.embed(texts=["hello"], model="text-embedding-3-large")

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.cohere"

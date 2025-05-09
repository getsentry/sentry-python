import itertools
from unittest import mock

import pytest
from huggingface_hub import (
    InferenceClient,
)
from huggingface_hub.errors import OverloadedError

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.huggingface_hub import HuggingfaceHubIntegration


def mock_client_post(client, post_mock):
    # huggingface-hub==0.28.0 deprecates the `post` method
    # so patch `_inner_post` instead
    client.post = post_mock
    client._inner_post = post_mock


@pytest.mark.parametrize(
    "send_default_pii, include_prompts, details_arg",
    itertools.product([True, False], repeat=3),
)
def test_nonstreaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts, details_arg
):
    sentry_init(
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = InferenceClient()
    if details_arg:
        post_mock = mock.Mock(
            return_value=b"""[{
                "generated_text": "the model response",
                "details": {
                    "finish_reason": "length",
                    "generated_tokens": 10,
                    "prefill": [],
                    "tokens": []
                }
            }]"""
        )
    else:
        post_mock = mock.Mock(
            return_value=b'[{"generated_text": "the model response"}]'
        )
    mock_client_post(client, post_mock)

    with start_transaction(name="huggingface_hub tx"):
        response = client.text_generation(
            prompt="hello",
            details=details_arg,
            stream=False,
        )
    if details_arg:
        assert response.generated_text == "the model response"
    else:
        assert response == "the model response"
    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "ai.chat_completions.create.huggingface_hub"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.AI_INPUT_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.AI_RESPONSES]
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["data"]
        assert SPANDATA.AI_RESPONSES not in span["data"]

    if details_arg:
        assert span["measurements"]["ai_total_tokens_used"]["value"] == 10


@pytest.mark.parametrize(
    "send_default_pii, include_prompts, details_arg",
    itertools.product([True, False], repeat=3),
)
def test_streaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts, details_arg
):
    sentry_init(
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    client = InferenceClient()

    post_mock = mock.Mock(
        return_value=[
            b"""data:{
                "token":{"id":1, "special": false, "text": "the model "}
            }""",
            b"""data:{
                "token":{"id":2, "special": false, "text": "response"},
                "details":{"finish_reason": "length", "generated_tokens": 10, "seed": 0}
            }""",
        ]
    )
    mock_client_post(client, post_mock)

    with start_transaction(name="huggingface_hub tx"):
        response = list(
            client.text_generation(
                prompt="hello",
                details=details_arg,
                stream=True,
            )
        )
    assert len(response) == 2
    if details_arg:
        assert response[0].token.text + response[1].token.text == "the model response"
    else:
        assert response[0] + response[1] == "the model response"

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "ai.chat_completions.create.huggingface_hub"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"][SPANDATA.AI_INPUT_MESSAGES]
        assert "the model response" in span["data"][SPANDATA.AI_RESPONSES]
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["data"]
        assert SPANDATA.AI_RESPONSES not in span["data"]

    if details_arg:
        assert span["measurements"]["ai_total_tokens_used"]["value"] == 10


def test_bad_chat_completion(sentry_init, capture_events):
    sentry_init(integrations=[HuggingfaceHubIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = InferenceClient()
    post_mock = mock.Mock(side_effect=OverloadedError("The server is overloaded"))
    mock_client_post(client, post_mock)

    with pytest.raises(OverloadedError):
        client.text_generation(prompt="hello")

    (event,) = events
    assert event["level"] == "error"


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[HuggingfaceHubIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = InferenceClient()
    post_mock = mock.Mock(
        return_value=[
            b"""data:{
                "token":{"id":1, "special": false, "text": "the model "}
            }""",
        ]
    )
    mock_client_post(client, post_mock)

    with start_transaction(name="huggingface_hub tx"):
        list(
            client.text_generation(
                prompt="hello",
                stream=True,
            )
        )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.huggingface_hub"

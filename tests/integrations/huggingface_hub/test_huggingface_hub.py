import itertools
import json

import pytest
from huggingface_hub import (
    InferenceClient,
    TextGenerationOutput,
    TextGenerationOutputDetails,
    TextGenerationStreamOutput,
    TextGenerationOutputToken,
    TextGenerationStreamDetails,
)
from huggingface_hub.errors import OverloadedError

from sentry_sdk import start_transaction
from sentry_sdk.integrations.huggingface_hub import HuggingfaceHubIntegration

from unittest import mock  # python 3.3 and above


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

    client = InferenceClient("some-model")
    if details_arg:
        client.post = mock.Mock(
            return_value=json.dumps(
                [
                    TextGenerationOutput(
                        generated_text="the model response",
                        details=TextGenerationOutputDetails(
                            finish_reason="TextGenerationFinishReason",
                            generated_tokens=10,
                            prefill=[],
                            tokens=[],  # not needed for integration
                        ),
                    )
                ]
            ).encode("utf-8")
        )
    else:
        client.post = mock.Mock(
            return_value=b'[{"generated_text": "the model response"}]'
        )
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
        assert "hello" in span["data"]["ai.input_messages"]
        assert "the model response" in span["data"]["ai.responses"]
    else:
        assert "ai.input_messages" not in span["data"]
        assert "ai.responses" not in span["data"]

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

    client = InferenceClient("some-model")
    client.post = mock.Mock(
        return_value=[
            b"data:"
            + json.dumps(
                TextGenerationStreamOutput(
                    token=TextGenerationOutputToken(
                        id=1, special=False, text="the model "
                    ),
                ),
            ).encode("utf-8"),
            b"data:"
            + json.dumps(
                TextGenerationStreamOutput(
                    token=TextGenerationOutputToken(
                        id=2, special=False, text="response"
                    ),
                    details=TextGenerationStreamDetails(
                        finish_reason="length",
                        generated_tokens=10,
                        seed=0,
                    ),
                )
            ).encode("utf-8"),
        ]
    )
    with start_transaction(name="huggingface_hub tx"):
        response = list(
            client.text_generation(
                prompt="hello",
                details=details_arg,
                stream=True,
            )
        )
    assert len(response) == 2
    print(response)
    if details_arg:
        assert response[0].token.text + response[1].token.text == "the model response"
    else:
        assert response[0] + response[1] == "the model response"

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "ai.chat_completions.create.huggingface_hub"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"]["ai.input_messages"]
        assert "the model response" in span["data"]["ai.responses"]
    else:
        assert "ai.input_messages" not in span["data"]
        assert "ai.responses" not in span["data"]

    if details_arg:
        assert span["measurements"]["ai_total_tokens_used"]["value"] == 10


def test_bad_chat_completion(sentry_init, capture_events):
    sentry_init(integrations=[HuggingfaceHubIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = InferenceClient("some-model")
    client.post = mock.Mock(side_effect=OverloadedError("The server is overloaded"))
    with pytest.raises(OverloadedError):
        client.text_generation(prompt="hello")

    (event,) = events
    assert event["level"] == "error"

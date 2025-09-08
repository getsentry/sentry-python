from unittest import mock
import pytest
import responses

from huggingface_hub import InferenceClient

import sentry_sdk

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


@pytest.fixture
def mock_hf_text_generation_api():
    # type: () -> Any
    """Mock HuggingFace text generation API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        rsps.add(
            responses.GET,
            f"https://huggingface.co/api/models/{model_name}",
            json={
                "id": model_name,
                "pipeline_tag": "text-generation",
                "inferenceProviderMapping": {
                    "hf-inference": {
                        "status": "live",
                        "providerId": model_name,
                        "task": "text-generation",
                    }
                },
            },
            status=200,
        )

        # Mock text generation endpoint
        rsps.add(
            responses.POST,
            f"https://router.huggingface.co/hf-inference/models/{model_name}",
            json={
                "generated_text": "Mocked response",
                "details": {
                    "finish_reason": "length",
                    "generated_tokens": 10,
                    "prefill": [],
                    "tokens": [],
                },
            },
            status=200,
        )

        yield rsps


@pytest.fixture
def mock_hf_chat_completion_api():
    # type: () -> Any
    """Mock HuggingFace chat completion API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        rsps.add(
            responses.GET,
            f"https://huggingface.co/api/models/{model_name}",
            json={
                "id": model_name,
                "pipeline_tag": "conversational",
                "inferenceProviderMapping": {
                    "hf-inference": {
                        "status": "live",
                        "providerId": model_name,
                        "task": "conversational",
                    }
                },
            },
            status=200,
        )

        # Mock chat completion endpoint
        rsps.add(
            responses.POST,
            f"https://router.huggingface.co/hf-inference/models/{model_name}/v1/chat/completions",
            json={
                "id": "xyz-123",
                "created": 1234567890,
                "model": f"{model_name}-123",
                "system_fingerprint": "fp_123",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Hello! How can I help you today?",
                        },
                    }
                ],
                "usage": {
                    "completion_tokens": 8,
                    "prompt_tokens": 10,
                    "total_tokens": 18,
                },
            },
            status=200,
        )

        yield rsps


def test_text_generation(sentry_init, capture_events, mock_hf_text_generation_api):
    # type: (Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    client = InferenceClient(
        model="test-model",
    )

    with sentry_sdk.start_transaction(name="test"):
        client.text_generation(
            prompt="Hello",
            stream=False,
            details=True,
        )

    (transaction,) = events
    (span,) = transaction["spans"]

    assert span["op"] == "gen_ai.generate_text"
    assert span["description"] == "generate_text test-model"
    assert span["data"] == {
        "gen_ai.operation.name": "generate_text",
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "length",
        "gen_ai.response.streaming": False,
        "gen_ai.usage.total_tokens": 10,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }


def test_chat_completion(sentry_init, capture_events, mock_hf_chat_completion_api):
    # type: (Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    client = InferenceClient(
        model="test-model",
    )

    with sentry_sdk.start_transaction(name="test"):
        client.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}],
            stream=False,
        )

    (transaction,) = events
    (span,) = transaction["spans"]

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"
    assert span["data"] == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "stop",
        "gen_ai.response.model": "test-model-123",
        "gen_ai.response.streaming": False,
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 8,
        "gen_ai.usage.total_tokens": 18,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

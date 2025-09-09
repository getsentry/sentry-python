from unittest import mock
import pytest
import responses

import huggingface_hub
from huggingface_hub import InferenceClient

import sentry_sdk
from sentry_sdk.utils import package_version

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


HF_VERSION = package_version("huggingface-hub")

if HF_VERSION and HF_VERSION < (0, 30, 0):
    MODEL_ENDPOINT = "https://api-inference.huggingface.co/models/{model_name}"
    INFERENCE_ENDPOINT = "https://api-inference.huggingface.co/models/{model_name}"
else:
    MODEL_ENDPOINT = "https://huggingface.co/api/models/{model_name}"
    INFERENCE_ENDPOINT = (
        "https://router.huggingface.co/hf-inference/models/{model_name}"
    )


@pytest.fixture
def mock_hf_text_generation_api():
    # type: () -> Any
    """Mock HuggingFace text generation API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        rsps.add(
            responses.GET,
            MODEL_ENDPOINT.format(model_name=model_name),
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
            INFERENCE_ENDPOINT.format(model_name=model_name),
            json={
                "generated_text": "[mocked] Hello! How can i help you?",
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
def mock_hf_api_with_errors():
    # type: () -> Any
    """Mock HuggingFace API that always raises errors for any request"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint with error
        rsps.add(
            responses.GET,
            MODEL_ENDPOINT.format(model_name=model_name),
            json={"error": "Model not found"},
            status=404,
        )

        # Mock text generation endpoint with error
        rsps.add(
            responses.POST,
            INFERENCE_ENDPOINT.format(model_name=model_name),
            json={"error": "Internal server error", "message": "Something went wrong"},
            status=500,
        )

        # Mock chat completion endpoint with error
        rsps.add(
            responses.POST,
            INFERENCE_ENDPOINT.format(model_name=model_name) + "/v1/chat/completions",
            json={"error": "Internal server error", "message": "Something went wrong"},
            status=500,
        )

        # Catch-all pattern for any other model requests
        rsps.add(
            responses.GET,
            "https://huggingface.co/api/models/test-model-error",
            json={"error": "Generic model error"},
            status=500,
        )

        yield rsps


@pytest.fixture
def mock_hf_text_generation_api_streaming():
    # type: () -> Any
    """Mock streaming HuggingFace text generation API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        rsps.add(
            responses.GET,
            MODEL_ENDPOINT.format(model_name=model_name),
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

        # Mock text generation endpoint for streaming
        streaming_response = b'data:{"token":{"id":1, "special": false, "text": "the mocked "}}\n\ndata:{"token":{"id":2, "special": false, "text": "model response"}, "details":{"finish_reason": "length", "generated_tokens": 10, "seed": 0}}\n\n'

        rsps.add(
            responses.POST,
            INFERENCE_ENDPOINT.format(model_name=model_name),
            body=streaming_response,
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
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
            MODEL_ENDPOINT.format(model_name=model_name),
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
            INFERENCE_ENDPOINT.format(model_name=model_name) + "/v1/chat/completions",
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
                            "content": "[mocked] Hello! How can I help you today?",
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


@pytest.fixture
def mock_hf_chat_completion_api_streaming():
    # type: () -> Any
    """Mock streaming HuggingFace chat completion API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        rsps.add(
            responses.GET,
            MODEL_ENDPOINT.format(model_name=model_name),
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

        # Mock chat completion streaming endpoint
        streaming_chat_response = (
            b'data:{"id":"xyz-123","created":1234567890,"model":"test-model-123","system_fingerprint":"fp_123","choices":[{"delta":{"role":"assistant","content":"the mocked "},"index":0,"finish_reason":null}],"usage":null}\n\n'
            b'data:{"id":"xyz-124","created":1234567890,"model":"test-model-123","system_fingerprint":"fp_123","choices":[{"delta":{"role":"assistant","content":"model response"},"index":0,"finish_reason":"stop"}],"usage":{"prompt_tokens":183,"completion_tokens":14,"total_tokens":197}}\n\n'
        )

        rsps.add(
            responses.POST,
            INFERENCE_ENDPOINT.format(model_name=model_name) + "/v1/chat/completions",
            body=streaming_chat_response,
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        yield rsps


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_text_generation(
    sentry_init, capture_events, send_default_pii, mock_hf_text_generation_api
):
    # type: (Any, Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0, send_default_pii=send_default_pii)
    events = capture_events()

    client = InferenceClient(model="test-model")

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

    expected_data = {
        "gen_ai.operation.name": "generate_text",
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "length",
        "gen_ai.response.streaming": False,
        "gen_ai.usage.total_tokens": 10,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    if send_default_pii:
        expected_data["gen_ai.request.messages"] = "Hello"
        expected_data["gen_ai.response.text"] = "[mocked] Hello! How can i help you?"

    if not send_default_pii:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data

    # text generation does not set the response model
    assert "gen_ai.response.model" not in span["data"]


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_text_generation_streaming(
    sentry_init, capture_events, send_default_pii, mock_hf_text_generation_api_streaming
):
    # type: (Any, Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0, send_default_pii=send_default_pii)
    events = capture_events()

    client = InferenceClient(model="test-model")

    with sentry_sdk.start_transaction(name="test"):
        for _ in client.text_generation(
            prompt="Hello",
            stream=True,
            details=True,
        ):
            pass

    (transaction,) = events
    (span,) = transaction["spans"]

    assert span["op"] == "gen_ai.generate_text"
    assert span["description"] == "generate_text test-model"

    expected_data = {
        "gen_ai.operation.name": "generate_text",
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "length",
        "gen_ai.response.streaming": True,
        "gen_ai.usage.total_tokens": 10,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    if send_default_pii:
        expected_data["gen_ai.request.messages"] = "Hello"
        expected_data["gen_ai.response.text"] = "the mocked model response"

    if not send_default_pii:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data

    # text generation does not set the response model
    assert "gen_ai.response.model" not in span["data"]


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_chat_completion(
    sentry_init, capture_events, send_default_pii, mock_hf_chat_completion_api
):
    # type: (Any, Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0, send_default_pii=send_default_pii)
    events = capture_events()

    client = InferenceClient(model="test-model")

    with sentry_sdk.start_transaction(name="test"):
        client.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}],
            stream=False,
        )

    (transaction,) = events
    (span,) = transaction["spans"]

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"

    expected_data = {
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

    if send_default_pii:
        expected_data["gen_ai.request.messages"] = (
            '[{"role": "user", "content": "Hello!"}]'
        )
        expected_data["gen_ai.response.text"] = (
            "[mocked] Hello! How can I help you today?"
        )

    if not send_default_pii:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_chat_completion_streaming(
    sentry_init, capture_events, send_default_pii, mock_hf_chat_completion_api_streaming
):
    # type: (Any, Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0, send_default_pii=send_default_pii)
    events = capture_events()

    client = InferenceClient(model="test-model")

    with sentry_sdk.start_transaction(name="test"):
        response = client.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}],
            stream=True,
        )

        for x in response:
            print(x)

    (transaction,) = events
    (span,) = transaction["spans"]

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"

    expected_data = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "stop",
        "gen_ai.response.model": "test-model-123",
        "gen_ai.response.streaming": True,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }
    # usage is not available in older versions of the library
    if HF_VERSION and HF_VERSION >= (0, 26, 0):
        expected_data["gen_ai.usage.input_tokens"] = 183
        expected_data["gen_ai.usage.output_tokens"] = 14
        expected_data["gen_ai.usage.total_tokens"] = 197

    if send_default_pii:
        expected_data["gen_ai.request.messages"] = (
            '[{"role": "user", "content": "Hello!"}]'
        )
        expected_data["gen_ai.response.text"] = "the mocked model response"

    if not send_default_pii:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data


def test_chat_completion_api_error(
    sentry_init, capture_events, mock_hf_api_with_errors
):
    # type: (Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    client = InferenceClient(model="test-model")

    with sentry_sdk.start_transaction(name="test"):
        with pytest.raises(huggingface_hub.errors.HfHubHTTPError):
            client.chat_completion(
                messages=[{"role": "user", "content": "Hello!"}],
            )

    (
        error,
        transaction,
    ) = events

    assert error["exception"]["values"][0]["mechanism"]["type"] == "huggingface_hub"
    assert not error["exception"]["values"][0]["mechanism"]["handled"]

    (span,) = transaction["spans"]

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"
    assert span.get("tags", {}).get("status") == "error"

    assert (
        error["contexts"]["trace"]["trace_id"]
        == transaction["contexts"]["trace"]["trace_id"]
    )
    expected_data = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "test-model",
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }
    assert span["data"] == expected_data

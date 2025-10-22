from unittest import mock
import pytest
import re
import responses
import httpx

from huggingface_hub import InferenceClient

import sentry_sdk
from sentry_sdk.utils import package_version
from sentry_sdk.integrations.huggingface_hub import HuggingfaceHubIntegration

from typing import TYPE_CHECKING

try:
    from huggingface_hub.utils._errors import HfHubHTTPError
except ImportError:
    from huggingface_hub.errors import HfHubHTTPError


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


def _add_mock_response(
    httpx_mock, rsps, method, url, json=None, status=200, body=None, headers=None
):
    # HF v1+ uses httpx for making requests to their API, while <1 uses requests.
    # Since we have to test both, we need mocks for both httpx and requests.
    if HF_VERSION >= (1, 0, 0):
        httpx_mock.add_response(
            method=method,
            url=url,
            json=json,
            content=body,
            status_code=status,
            headers=headers,
            is_optional=True,
            is_reusable=True,
        )
    else:
        rsps.add(
            method=method,
            url=url,
            json=json,
            body=body,
            status=status,
            headers=headers,
        )


@pytest.fixture
def mock_hf_text_generation_api(httpx_mock):
    # type: () -> Any
    """Mock HuggingFace text generation API"""

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
            re.compile(
                MODEL_ENDPOINT.format(model_name=model_name)
                + r"(\?expand=inferenceProviderMapping)?"
            ),
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

        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
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

        if HF_VERSION >= (1, 0, 0):
            yield httpx_mock
        else:
            yield rsps


@pytest.fixture
def mock_hf_api_with_errors(httpx_mock):
    # type: () -> Any
    """Mock HuggingFace API that always raises errors for any request"""

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint with error
        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
            MODEL_ENDPOINT.format(model_name=model_name),
            json={"error": "Model not found"},
            status=404,
        )

        # Mock text generation endpoint with error
        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
            INFERENCE_ENDPOINT.format(model_name=model_name),
            json={"error": "Internal server error", "message": "Something went wrong"},
            status=500,
        )

        # Mock chat completion endpoint with error
        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
            INFERENCE_ENDPOINT.format(model_name=model_name) + "/v1/chat/completions",
            json={"error": "Internal server error", "message": "Something went wrong"},
            status=500,
        )

        # Catch-all pattern for any other model requests
        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
            "https://huggingface.co/api/models/test-model-error",
            json={"error": "Generic model error"},
            status=500,
        )

        if HF_VERSION >= (1, 0, 0):
            yield httpx_mock
        else:
            yield rsps


@pytest.fixture
def mock_hf_text_generation_api_streaming(httpx_mock):
    # type: () -> Any
    """Mock streaming HuggingFace text generation API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
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

        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
            INFERENCE_ENDPOINT.format(model_name=model_name),
            body=streaming_response,
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        if HF_VERSION >= (1, 0, 0):
            yield httpx_mock
        else:
            yield rsps


@pytest.fixture
def mock_hf_chat_completion_api(httpx_mock):
    # type: () -> Any
    """Mock HuggingFace chat completion API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
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
        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
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

        if HF_VERSION >= (1, 0, 0):
            yield httpx_mock
        else:
            yield rsps


@pytest.fixture
def mock_hf_chat_completion_api_tools(httpx_mock):
    # type: () -> Any
    """Mock HuggingFace chat completion API with tool calls."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
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
        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
            INFERENCE_ENDPOINT.format(model_name=model_name) + "/v1/chat/completions",
            json={
                "id": "xyz-123",
                "created": 1234567890,
                "model": f"{model_name}-123",
                "system_fingerprint": "fp_123",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": {"location": "Paris"},
                                    },
                                }
                            ],
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

        if HF_VERSION >= (1, 0, 0):
            yield httpx_mock
        else:
            yield rsps


@pytest.fixture
def mock_hf_chat_completion_api_streaming(httpx_mock):
    # type: () -> Any
    """Mock streaming HuggingFace chat completion API"""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
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

        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
            INFERENCE_ENDPOINT.format(model_name=model_name) + "/v1/chat/completions",
            body=streaming_chat_response,
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        if HF_VERSION >= (1, 0, 0):
            yield httpx_mock
        else:
            yield rsps


@pytest.fixture
def mock_hf_chat_completion_api_streaming_tools(httpx_mock):
    # type: () -> Any
    """Mock streaming HuggingFace chat completion API with tool calls."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        model_name = "test-model"

        # Mock model info endpoint
        _add_mock_response(
            httpx_mock,
            rsps,
            "GET",
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
            b'data:{"id":"xyz-123","created":1234567890,"model":"test-model-123","system_fingerprint":"fp_123","choices":[{"delta":{"role":"assistant","content":"response with tool calls follows"},"index":0,"finish_reason":null}],"usage":null}\n\n'
            b'data:{"id":"xyz-124","created":1234567890,"model":"test-model-123","system_fingerprint":"fp_123","choices":[{"delta":{"role":"assistant","tool_calls": [{"id": "call_123","type": "function","function": {"name": "get_weather", "arguments": {"location": "Paris"}}}]},"index":0,"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":183,"completion_tokens":14,"total_tokens":197}}\n\n'
        )

        _add_mock_response(
            httpx_mock,
            rsps,
            "POST",
            INFERENCE_ENDPOINT.format(model_name=model_name) + "/v1/chat/completions",
            body=streaming_chat_response,
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        if HF_VERSION >= (1, 0, 0):
            yield httpx_mock
        else:
            yield rsps


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("include_prompts", [True, False])
def test_text_generation(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    mock_hf_text_generation_api,
):
    # type: (Any, Any, Any, Any, Any) -> None
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
    )
    events = capture_events()

    client = InferenceClient(model="test-model")

    with sentry_sdk.start_transaction(name="test"):
        client.text_generation(
            "Hello",
            stream=False,
            details=True,
        )

    (transaction,) = events

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None

    assert span["op"] == "gen_ai.generate_text"
    assert span["description"] == "generate_text test-model"
    assert span["origin"] == "auto.ai.huggingface_hub"

    expected_data = {
        "gen_ai.operation.name": "generate_text",
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "length",
        "gen_ai.response.streaming": False,
        "gen_ai.usage.total_tokens": 10,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    if send_default_pii and include_prompts:
        expected_data["gen_ai.request.messages"] = "Hello"
        expected_data["gen_ai.response.text"] = "[mocked] Hello! How can i help you?"

    if not send_default_pii or not include_prompts:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data

    # text generation does not set the response model
    assert "gen_ai.response.model" not in span["data"]


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("include_prompts", [True, False])
def test_text_generation_streaming(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    mock_hf_text_generation_api_streaming,
):
    # type: (Any, Any, Any, Any, Any) -> None
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
    )
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

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None

    assert span["op"] == "gen_ai.generate_text"
    assert span["description"] == "generate_text test-model"
    assert span["origin"] == "auto.ai.huggingface_hub"

    expected_data = {
        "gen_ai.operation.name": "generate_text",
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "length",
        "gen_ai.response.streaming": True,
        "gen_ai.usage.total_tokens": 10,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    if send_default_pii and include_prompts:
        expected_data["gen_ai.request.messages"] = "Hello"
        expected_data["gen_ai.response.text"] = "the mocked model response"

    if not send_default_pii or not include_prompts:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data

    # text generation does not set the response model
    assert "gen_ai.response.model" not in span["data"]


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("include_prompts", [True, False])
def test_chat_completion(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    mock_hf_chat_completion_api,
):
    # type: (Any, Any, Any, Any, Any) -> None
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
    )
    events = capture_events()

    client = InferenceClient(model="test-model", provider="hf-inference")

    with sentry_sdk.start_transaction(name="test"):
        client.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}],
            stream=False,
        )

    (transaction,) = events

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"
    assert span["origin"] == "auto.ai.huggingface_hub"

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

    if send_default_pii and include_prompts:
        expected_data["gen_ai.request.messages"] = (
            '[{"role": "user", "content": "Hello!"}]'
        )
        expected_data["gen_ai.response.text"] = (
            "[mocked] Hello! How can I help you today?"
        )

    if not send_default_pii or not include_prompts:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("include_prompts", [True, False])
def test_chat_completion_streaming(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    mock_hf_chat_completion_api_streaming,
):
    # type: (Any, Any, Any, Any, Any) -> None
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
    )
    events = capture_events()

    client = InferenceClient(model="test-model", provider="hf-inference")

    with sentry_sdk.start_transaction(name="test"):
        _ = list(
            client.chat_completion(
                [{"role": "user", "content": "Hello!"}],
                stream=True,
            )
        )

    (transaction,) = events

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"
    assert span["origin"] == "auto.ai.huggingface_hub"

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

    if send_default_pii and include_prompts:
        expected_data["gen_ai.request.messages"] = (
            '[{"role": "user", "content": "Hello!"}]'
        )
        expected_data["gen_ai.response.text"] = "the mocked model response"

    if not send_default_pii or not include_prompts:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data

    assert span["data"] == expected_data


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_chat_completion_api_error(
    sentry_init, capture_events, mock_hf_api_with_errors
):
    # type: (Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    client = InferenceClient(model="test-model", provider="hf-inference")

    with sentry_sdk.start_transaction(name="test"):
        with pytest.raises(HfHubHTTPError):
            client.chat_completion(
                messages=[{"role": "user", "content": "Hello!"}],
            )

    (
        error,
        transaction,
    ) = events

    assert error["exception"]["values"][0]["mechanism"]["type"] == "huggingface_hub"
    assert not error["exception"]["values"][0]["mechanism"]["handled"]

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"
    assert span["origin"] == "auto.ai.huggingface_hub"
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


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
def test_span_status_error(sentry_init, capture_events, mock_hf_api_with_errors):
    # type: (Any, Any, Any) -> None
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    client = InferenceClient(model="test-model", provider="hf-inference")

    with sentry_sdk.start_transaction(name="test"):
        with pytest.raises(HfHubHTTPError):
            client.chat_completion(
                messages=[{"role": "user", "content": "Hello!"}],
            )

    (error, transaction) = events
    assert error["level"] == "error"

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None
    assert span["tags"]["status"] == "error"

    assert transaction["contexts"]["trace"]["status"] == "error"


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("include_prompts", [True, False])
def test_chat_completion_with_tools(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    mock_hf_chat_completion_api_tools,
):
    # type: (Any, Any, Any, Any, Any) -> None
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
    )
    events = capture_events()

    client = InferenceClient(model="test-model", provider="hf-inference")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
        }
    ]

    with sentry_sdk.start_transaction(name="test"):
        client.chat_completion(
            messages=[{"role": "user", "content": "What is the weather in Paris?"}],
            tools=tools,
            tool_choice="auto",
        )

    (transaction,) = events

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"
    assert span["origin"] == "auto.ai.huggingface_hub"

    expected_data = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.available_tools": '[{"type": "function", "function": {"name": "get_weather", "description": "Get current weather", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}}]',
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "tool_calls",
        "gen_ai.response.model": "test-model-123",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 8,
        "gen_ai.usage.total_tokens": 18,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    if send_default_pii and include_prompts:
        expected_data["gen_ai.request.messages"] = (
            '[{"role": "user", "content": "What is the weather in Paris?"}]'
        )
        expected_data["gen_ai.response.tool_calls"] = (
            '[{"function": {"arguments": {"location": "Paris"}, "name": "get_weather", "description": "None"}, "id": "call_123", "type": "function"}]'
        )

    if not send_default_pii or not include_prompts:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data
        assert "gen_ai.response.tool_calls" not in expected_data

    assert span["data"] == expected_data


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("include_prompts", [True, False])
def test_chat_completion_streaming_with_tools(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    mock_hf_chat_completion_api_streaming_tools,
):
    # type: (Any, Any, Any, Any, Any) -> None
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        integrations=[HuggingfaceHubIntegration(include_prompts=include_prompts)],
    )
    events = capture_events()

    client = InferenceClient(model="test-model", provider="hf-inference")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
        }
    ]

    with sentry_sdk.start_transaction(name="test"):
        _ = list(
            client.chat_completion(
                messages=[{"role": "user", "content": "What is the weather in Paris?"}],
                stream=True,
                tools=tools,
                tool_choice="auto",
            )
        )

    (transaction,) = events

    span = None
    for sp in transaction["spans"]:
        if sp["op"].startswith("gen_ai"):
            assert span is None, "there is exactly one gen_ai span"
            span = sp
        else:
            # there should be no other spans, just the gen_ai span
            # and optionally some http.client spans from talking to the hf api
            assert sp["op"] == "http.client"

    assert span is not None

    assert span["op"] == "gen_ai.chat"
    assert span["description"] == "chat test-model"
    assert span["origin"] == "auto.ai.huggingface_hub"

    expected_data = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.available_tools": '[{"type": "function", "function": {"name": "get_weather", "description": "Get current weather", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}}]',
        "gen_ai.request.model": "test-model",
        "gen_ai.response.finish_reasons": "tool_calls",
        "gen_ai.response.model": "test-model-123",
        "gen_ai.response.streaming": True,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    if HF_VERSION and HF_VERSION >= (0, 26, 0):
        expected_data["gen_ai.usage.input_tokens"] = 183
        expected_data["gen_ai.usage.output_tokens"] = 14
        expected_data["gen_ai.usage.total_tokens"] = 197

    if send_default_pii and include_prompts:
        expected_data["gen_ai.request.messages"] = (
            '[{"role": "user", "content": "What is the weather in Paris?"}]'
        )
        expected_data["gen_ai.response.text"] = "response with tool calls follows"
        expected_data["gen_ai.response.tool_calls"] = (
            '[{"function": {"arguments": {"location": "Paris"}, "name": "get_weather"}, "id": "call_123", "type": "function", "index": "None"}]'
        )

    if not send_default_pii or not include_prompts:
        assert "gen_ai.request.messages" not in expected_data
        assert "gen_ai.response.text" not in expected_data
        assert "gen_ai.response.tool_calls" not in expected_data

    assert span["data"] == expected_data

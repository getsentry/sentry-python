import pytest
from unittest import mock
from datetime import datetime

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


try:
    import litellm
except ImportError:
    pytest.skip("litellm not installed", allow_module_level=True)

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.litellm import (
    LiteLLMIntegration,
    _input_callback,
    _success_callback,
    _failure_callback,
)
from sentry_sdk.utils import package_version


LITELLM_VERSION = package_version("litellm")


# Mock response objects
class MockMessage:
    def __init__(self, role="assistant", content="Test response"):
        self.role = role
        self.content = content
        self.tool_calls = None

    def model_dump(self):
        return {"role": self.role, "content": self.content}


class MockChoice:
    def __init__(self, message=None):
        self.message = message or MockMessage()
        self.index = 0
        self.finish_reason = "stop"


class MockUsage:
    def __init__(self, prompt_tokens=10, completion_tokens=20, total_tokens=30):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class MockCompletionResponse:
    def __init__(
        self,
        model="gpt-3.5-turbo",
        choices=None,
        usage=None,
    ):
        self.id = "chatcmpl-test"
        self.model = model
        self.choices = choices or [MockChoice()]
        self.usage = usage or MockUsage()
        self.object = "chat.completion"
        self.created = 1234567890


class MockEmbeddingData:
    def __init__(self, embedding=None):
        self.embedding = embedding or [0.1, 0.2, 0.3]
        self.index = 0
        self.object = "embedding"


class MockEmbeddingResponse:
    def __init__(self, model="text-embedding-ada-002", data=None, usage=None):
        self.model = model
        self.data = data or [MockEmbeddingData()]
        self.usage = usage or MockUsage(
            prompt_tokens=5, completion_tokens=0, total_tokens=5
        )
        self.object = "list"


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_nonstreaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]
    mock_response = MockCompletionResponse()

    with start_transaction(name="litellm test"):
        # Simulate what litellm does: call input callback, then success callback
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert event["transaction"] == "litellm test"

    assert len(event["spans"]) == 1
    (span,) = event["spans"]

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat gpt-3.5-turbo"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gpt-3.5-turbo"
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "gpt-3.5-turbo"
    assert span["data"][SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in span["data"]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_streaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]
    mock_response = MockCompletionResponse()

    with start_transaction(name="litellm test"):
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
            "stream": True,
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert len(event["spans"]) == 1
    (span,) = event["spans"]

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


def test_embeddings_create(sentry_init, capture_events):
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_response = MockEmbeddingResponse()

    with start_transaction(name="litellm test"):
        # For embeddings, messages would be empty
        kwargs = {
            "model": "text-embedding-ada-002",
            "input": "Hello!",
            "messages": [],  # Empty for embeddings
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert len(event["spans"]) == 1
    (span,) = event["spans"]

    assert span["op"] == OP.GEN_AI_EMBEDDINGS
    assert span["description"] == "embeddings text-embedding-ada-002"
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5


def test_exception_handling(sentry_init, capture_events):
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]

    with start_transaction(name="litellm test"):
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
        }

        _input_callback(kwargs)
        _failure_callback(
            kwargs,
            Exception("API rate limit reached"),
            datetime.now(),
            datetime.now(),
        )

    # Should have error event and transaction
    assert len(events) >= 1
    # Find the error event
    error_events = [e for e in events if e.get("level") == "error"]
    assert len(error_events) == 1


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]
    mock_response = MockCompletionResponse()

    with start_transaction(name="litellm test"):
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.litellm"


def test_multiple_providers(sentry_init, capture_events):
    """Test that the integration correctly identifies different providers."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]

    # Test with different model prefixes
    test_cases = [
        ("gpt-3.5-turbo", "openai"),
        ("claude-3-opus-20240229", "anthropic"),
        ("gemini/gemini-pro", "gemini"),
    ]

    for model, _ in test_cases:
        mock_response = MockCompletionResponse(model=model)
        with start_transaction(name=f"test {model}"):
            kwargs = {
                "model": model,
                "messages": messages,
            }

            _input_callback(kwargs)
            _success_callback(
                kwargs,
                mock_response,
                datetime.now(),
                datetime.now(),
            )

    assert len(events) == len(test_cases)

    for i in range(len(test_cases)):
        span = events[i]["spans"][0]
        # The provider should be detected by litellm.get_llm_provider
        assert SPANDATA.GEN_AI_SYSTEM in span["data"]


def test_additional_parameters(sentry_init, capture_events):
    """Test that additional parameters are captured."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]
    mock_response = MockCompletionResponse()

    with start_transaction(name="litellm test"):
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 100,
            "top_p": 0.9,
            "frequency_penalty": 0.5,
            "presence_penalty": 0.5,
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    (event,) = events
    (span,) = event["spans"]

    assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.5
    assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.5


def test_litellm_specific_parameters(sentry_init, capture_events):
    """Test that LiteLLM-specific parameters are captured."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]
    mock_response = MockCompletionResponse()

    with start_transaction(name="litellm test"):
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
            "api_base": "https://custom-api.example.com",
            "api_version": "2023-01-01",
            "custom_llm_provider": "custom_provider",
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    (event,) = events
    (span,) = event["spans"]

    assert span["data"]["gen_ai.litellm.api_base"] == "https://custom-api.example.com"
    assert span["data"]["gen_ai.litellm.api_version"] == "2023-01-01"
    assert span["data"]["gen_ai.litellm.custom_llm_provider"] == "custom_provider"


def test_no_integration(sentry_init, capture_events):
    """Test that when integration is not enabled, callbacks don't break."""
    sentry_init(
        traces_sample_rate=1.0,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]
    mock_response = MockCompletionResponse()

    with start_transaction(name="litellm test"):
        # When the integration isn't enabled, the callbacks should exit early
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
        }

        # These should not crash, just do nothing
        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    (event,) = events
    # Should still have the transaction, but no child spans since integration is off
    assert event["type"] == "transaction"
    assert len(event.get("spans", [])) == 0


def test_response_without_usage(sentry_init, capture_events):
    """Test handling of responses without usage information."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]

    # Create a mock response without usage
    mock_response = type(
        "obj",
        (object,),
        {
            "model": "gpt-3.5-turbo",
            "choices": [MockChoice()],
        },
    )()

    with start_transaction(name="litellm test"):
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    (event,) = events
    (span,) = event["spans"]

    # Span should still be created even without usage info
    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat gpt-3.5-turbo"


def test_integration_setup(sentry_init):
    """Test that the integration sets up the callbacks correctly."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )

    # Check that callbacks are registered
    assert _input_callback in (litellm.input_callback or [])
    assert _success_callback in (litellm.success_callback or [])
    assert _failure_callback in (litellm.failure_callback or [])


def test_message_dict_extraction(sentry_init, capture_events):
    """Test that response messages are properly extracted with dict() fallback."""
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    messages = [{"role": "user", "content": "Hello!"}]

    # Create a message that has dict() method instead of model_dump()
    class DictMessage:
        def __init__(self):
            self.role = "assistant"
            self.content = "Response"
            self.tool_calls = None

        def dict(self):
            return {"role": self.role, "content": self.content}

    mock_response = MockCompletionResponse(choices=[MockChoice(message=DictMessage())])

    with start_transaction(name="litellm test"):
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": messages,
        }

        _input_callback(kwargs)
        _success_callback(
            kwargs,
            mock_response,
            datetime.now(),
            datetime.now(),
        )

    (event,) = events
    (span,) = event["spans"]

    # Should have extracted the response message
    assert SPANDATA.GEN_AI_RESPONSE_TEXT in span["data"]

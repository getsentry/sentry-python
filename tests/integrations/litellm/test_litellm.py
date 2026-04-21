import json
import pytest
import time
import asyncio
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

from openai import OpenAI, AsyncOpenAI

from concurrent.futures import ThreadPoolExecutor

import litellm.utils as litellm_utils
from litellm.litellm_core_utils import streaming_handler
from litellm.litellm_core_utils import thread_pool_executor
from litellm.litellm_core_utils import litellm_logging
from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
from litellm.llms.custom_httpx.http_handler import HTTPHandler, AsyncHTTPHandler


LITELLM_VERSION = package_version("litellm")


def _reset_litellm_executor():
    thread_pool_executor.executor = ThreadPoolExecutor(max_workers=100)
    litellm_utils.executor = thread_pool_executor.executor
    streaming_handler.executor = thread_pool_executor.executor
    litellm_logging.executor = thread_pool_executor.executor


@pytest.fixture()
def reset_litellm_executor():
    yield
    _reset_litellm_executor()


@pytest.fixture
def clear_litellm_cache():
    """
    Clear litellm's client cache and reset integration state to ensure test isolation.

    The LiteLLM integration uses setup_once() which only runs once per Python process.
    This fixture ensures the integration is properly re-initialized for each test.
    """

    # Stop all existing mocks
    mock.patch.stopall()

    # Clear client cache
    if (
        hasattr(litellm, "in_memory_llm_clients_cache")
        and litellm.in_memory_llm_clients_cache
    ):
        litellm.in_memory_llm_clients_cache.flush_cache()

    yield

    # Clean up after test as well
    mock.patch.stopall()
    if (
        hasattr(litellm, "in_memory_llm_clients_cache")
        and litellm.in_memory_llm_clients_cache
    ):
        litellm.in_memory_llm_clients_cache.flush_cache()


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
    reset_litellm_executor,
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]

    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
            )

            litellm_utils.executor.shutdown(wait=True)

    (event,) = (item.payload for item in items if item.type == "transaction")
    assert event["transaction"] == "litellm test"

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 1
    span = chat_spans[0]

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat gpt-3.5-turbo"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gpt-3.5-turbo"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "gpt-3.5-turbo"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in span["attributes"]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_async_nonstreaming_chat_completion(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]

    client = AsyncOpenAI(api_key="test-key")

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

    (event,) = (item.payload for item in items if item.type == "transaction")
    assert event["transaction"] == "litellm test"

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 1
    span = chat_spans[0]

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat gpt-3.5-turbo"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gpt-3.5-turbo"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "gpt-3.5-turbo"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "openai"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in span["attributes"]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30


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
    reset_litellm_executor,
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    items = capture_items("span")

    messages = [{"role": "user", "content": "Hello!"}]

    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        server_side_event_chunks(
            streaming_chat_completions_model_response,
            include_event_type=False,
        ),
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
                stream=True,
            )
            for _ in response:
                pass

            streaming_handler.executor.shutdown(wait=True)

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 1
    span = chat_spans[0]

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_async_streaming_chat_completion(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
    streaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]

    client = AsyncOpenAI(api_key="test-key")

    model_response = get_model_response(
        async_iterator(
            server_side_event_chunks(
                streaming_chat_completions_model_response,
                include_event_type=False,
            ),
        ),
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
                stream=True,
            )
            async for _ in response:
                pass

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 1
    span = chat_spans[0]

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


def test_embeddings_create(
    sentry_init,
    capture_items,
    get_model_response,
    openai_embedding_model_response,
    clear_litellm_cache,
):
    """
    Test that litellm.embedding() calls are properly instrumented.

    This test calls the actual litellm.embedding() function (not just callbacks)
    to ensure proper integration testing.
    """
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    items = capture_items("transaction", "span")

    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        openai_embedding_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.embeddings._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = litellm.embedding(
                model="text-embedding-ada-002",
                input="Hello, world!",
                client=client,
            )
            # Allow time for callbacks to complete (they may run in separate threads)
            time.sleep(0.1)

        # Response is processed by litellm, so just check it exists
        assert response is not None

        spans = [item.payload for item in items if item.type == "span"]
        spans = list(
            x
            for x in spans
            if x["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
            and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
        )
        assert len(spans) == 1
        span = spans[0]

        assert span["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
        assert span["name"] == "embeddings text-embedding-ada-002"
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "text-embedding-ada-002"
        )
        # Check that embeddings input is captured (it's JSON serialized)
        embeddings_input = span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
        assert json.loads(embeddings_input) == ["Hello, world!"]


@pytest.mark.asyncio(loop_scope="session")
async def test_async_embeddings_create(
    sentry_init,
    capture_items,
    get_model_response,
    openai_embedding_model_response,
    clear_litellm_cache,
):
    """
    Test that litellm.embedding() calls are properly instrumented.

    This test calls the actual litellm.embedding() function (not just callbacks)
    to ensure proper integration testing.
    """
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    items = capture_items("transaction", "span")

    client = AsyncOpenAI(api_key="test-key")

    model_response = get_model_response(
        openai_embedding_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.embeddings._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = await litellm.aembedding(
                model="text-embedding-ada-002",
                input="Hello, world!",
                client=client,
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

        # Response is processed by litellm, so just check it exists
        assert response is not None

        spans = [item.payload for item in items if item.type == "span"]
        spans = list(
            x
            for x in spans
            if x["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
            and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
        )
        assert len(spans) == 1
        span = spans[0]

        assert span["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
        assert span["name"] == "embeddings text-embedding-ada-002"
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "text-embedding-ada-002"
        )
        # Check that embeddings input is captured (it's JSON serialized)
        embeddings_input = span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
        assert json.loads(embeddings_input) == ["Hello, world!"]


def test_embeddings_create_with_list_input(
    sentry_init,
    capture_items,
    get_model_response,
    openai_embedding_model_response,
    clear_litellm_cache,
):
    """Test embedding with list input."""
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    items = capture_items("transaction", "span")

    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        openai_embedding_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.embeddings._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = litellm.embedding(
                model="text-embedding-ada-002",
                input=["First text", "Second text", "Third text"],
                client=client,
            )
            # Allow time for callbacks to complete (they may run in separate threads)
            time.sleep(0.1)

        # Response is processed by litellm, so just check it exists
        assert response is not None

        spans = [item.payload for item in items if item.type == "span"]
        spans = list(
            x
            for x in spans
            if x["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
            and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
        )
        assert len(spans) == 1
        span = spans[0]

        assert span["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
        # Check that list of embeddings input is captured (it's JSON serialized)
        embeddings_input = span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
        assert json.loads(embeddings_input) == [
            "First text",
            "Second text",
            "Third text",
        ]


@pytest.mark.asyncio(loop_scope="session")
async def test_async_embeddings_create_with_list_input(
    sentry_init,
    capture_items,
    get_model_response,
    openai_embedding_model_response,
    clear_litellm_cache,
):
    """Test embedding with list input."""
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    items = capture_items("transaction", "span")

    client = AsyncOpenAI(api_key="test-key")

    model_response = get_model_response(
        openai_embedding_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.embeddings._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = await litellm.aembedding(
                model="text-embedding-ada-002",
                input=["First text", "Second text", "Third text"],
                client=client,
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

        # Response is processed by litellm, so just check it exists
        assert response is not None

        spans = [item.payload for item in items if item.type == "span"]
        spans = list(
            x
            for x in spans
            if x["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
            and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
        )
        assert len(spans) == 1
        span = spans[0]

        assert span["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
        # Check that list of embeddings input is captured (it's JSON serialized)
        embeddings_input = span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
        assert json.loads(embeddings_input) == [
            "First text",
            "Second text",
            "Third text",
        ]


def test_embeddings_no_pii(
    sentry_init,
    capture_items,
    get_model_response,
    openai_embedding_model_response,
    clear_litellm_cache,
):
    """Test that PII is not captured when disabled."""
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=False,  # PII disabled
    )
    items = capture_items("transaction", "span")

    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        openai_embedding_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.embeddings._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = litellm.embedding(
                model="text-embedding-ada-002",
                input="Hello, world!",
                client=client,
            )
            # Allow time for callbacks to complete (they may run in separate threads)
            time.sleep(0.1)

        # Response is processed by litellm, so just check it exists
        assert response is not None

        spans = [item.payload for item in items if item.type == "span"]
        spans = list(
            x
            for x in spans
            if x["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
            and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
        )
        assert len(spans) == 1
        span = spans[0]

        assert span["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
        # Check that embeddings input is NOT captured when PII is disabled
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["attributes"]


@pytest.mark.asyncio(loop_scope="session")
async def test_async_embeddings_no_pii(
    sentry_init,
    capture_items,
    get_model_response,
    openai_embedding_model_response,
    clear_litellm_cache,
):
    """Test that PII is not captured when disabled."""
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=False,  # PII disabled
    )
    items = capture_items("transaction", "span")

    client = AsyncOpenAI(api_key="test-key")

    model_response = get_model_response(
        openai_embedding_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.embeddings._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            response = await litellm.aembedding(
                model="text-embedding-ada-002",
                input="Hello, world!",
                client=client,
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

        # Response is processed by litellm, so just check it exists
        assert response is not None

        spans = [item.payload for item in items if item.type == "span"]
        spans = list(
            x
            for x in spans
            if x["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
            and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
        )
        assert len(spans) == 1
        span = spans[0]

        assert span["attributes"]["sentry.op"] == OP.GEN_AI_EMBEDDINGS
        # Check that embeddings input is NOT captured when PII is disabled
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in span["attributes"]


def test_exception_handling(
    reset_litellm_executor, sentry_init, capture_items, get_rate_limit_model_response
):
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("event")

    messages = [{"role": "user", "content": "Hello!"}]

    client = OpenAI(api_key="test-key")

    model_response = get_rate_limit_model_response()

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            with pytest.raises(litellm.RateLimitError):
                litellm.completion(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    client=client,
                )

    # Find the error event
    error_events = [
        item.payload
        for item in items
        if item.type == "event" and item.payload.get("level") == "error"
    ]
    assert len(error_events) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_async_exception_handling(
    sentry_init, capture_items, get_rate_limit_model_response
):
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("event")

    messages = [{"role": "user", "content": "Hello!"}]

    client = AsyncOpenAI(api_key="test-key")

    model_response = get_rate_limit_model_response()

    with mock.patch.object(
        client.embeddings._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            with pytest.raises(litellm.RateLimitError):
                await litellm.acompletion(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    client=client,
                )

    # Find the error event
    error_events = [
        item.payload
        for item in items
        if item.type == "event" and item.payload.get("level") == "error"
    ]
    assert len(error_events) == 1


def test_span_origin(
    reset_litellm_executor,
    sentry_init,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
):
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]

    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
            )

            litellm_utils.executor.shutdown(wait=True)

    (event,) = (item.payload for item in items if item.type == "transaction")
    assert event["contexts"]["trace"]["origin"] == "manual"

    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.litellm"


def test_multiple_providers(
    reset_litellm_executor,
    sentry_init,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
    nonstreaming_anthropic_model_response,
    nonstreaming_google_genai_model_response,
):
    """Test that the integration correctly identifies different providers."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction")

    messages = [{"role": "user", "content": "Hello!"}]

    openai_client = OpenAI(api_key="test-key")
    openai_model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        openai_client.completions._client._client,
        "send",
        return_value=openai_model_response,
    ):
        with start_transaction(name="test gpt-3.5-turbo"):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=openai_client,
            )

            litellm_utils.executor.shutdown(wait=True)

    _reset_litellm_executor()

    anthropic_client = HTTPHandler()
    anthropic_model_response = get_model_response(
        nonstreaming_anthropic_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        anthropic_client,
        "post",
        return_value=anthropic_model_response,
    ):
        with start_transaction(name="test claude-3-opus-20240229"):
            litellm.completion(
                model="claude-3-opus-20240229",
                messages=messages,
                client=anthropic_client,
                api_key="test-key",
            )

            litellm_utils.executor.shutdown(wait=True)

    _reset_litellm_executor()

    gemini_client = HTTPHandler()
    gemini_model_response = get_model_response(
        nonstreaming_google_genai_model_response,
        serialize_pydantic=True,
    )

    with mock.patch.object(
        gemini_client,
        "post",
        return_value=gemini_model_response,
    ):
        with start_transaction(name="test gemini/gemini-pro"):
            litellm.completion(
                model="gemini/gemini-pro",
                messages=messages,
                client=gemini_client,
                api_key="test-key",
            )

            litellm_utils.executor.shutdown(wait=True)

    events = [item.payload for item in items if item.type == "transaction"]
    assert len(events) == 3

    spans = [item.payload for item in items if item.type == "span"]
    for span in spans:
        # The provider should be detected by litellm.get_llm_provider
        assert SPANDATA.GEN_AI_SYSTEM in span["attributes"]


@pytest.mark.asyncio(loop_scope="session")
async def test_async_multiple_providers(
    sentry_init,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
    nonstreaming_anthropic_model_response,
    nonstreaming_google_genai_model_response,
):
    """Test that the integration correctly identifies different providers."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]

    openai_client = AsyncOpenAI(api_key="test-key")
    openai_model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        openai_client.completions._client._client,
        "send",
        return_value=openai_model_response,
    ):
        with start_transaction(name="test gpt-3.5-turbo"):
            await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=openai_client,
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

    _reset_litellm_executor()

    anthropic_client = AsyncHTTPHandler()
    anthropic_model_response = get_model_response(
        nonstreaming_anthropic_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "True"},
    )

    with mock.patch.object(
        anthropic_client,
        "post",
        return_value=anthropic_model_response,
    ):
        with start_transaction(name="test claude-3-opus-20240229"):
            await litellm.acompletion(
                model="claude-3-opus-20240229",
                messages=messages,
                client=anthropic_client,
                api_key="test-key",
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

    _reset_litellm_executor()

    gemini_client = AsyncHTTPHandler()
    gemini_model_response = get_model_response(
        nonstreaming_google_genai_model_response,
        serialize_pydantic=True,
    )

    with mock.patch.object(
        gemini_client,
        "post",
        return_value=gemini_model_response,
    ):
        with start_transaction(name="test gemini/gemini-pro"):
            await litellm.acompletion(
                model="gemini/gemini-pro",
                messages=messages,
                client=gemini_client,
                api_key="test-key",
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

    events = [item.payload for item in items if item.type == "transaction"]
    assert len(events) == 3

    spans = [item.payload for item in items if item.type == "span"]
    for span in spans:
        # The provider should be detected by litellm.get_llm_provider
        assert SPANDATA.GEN_AI_SYSTEM in span["attributes"]


def test_additional_parameters(
    reset_litellm_executor,
    sentry_init,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
):
    """Test that additional parameters are captured."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]
    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
                temperature=0.7,
                max_tokens=100,
                top_p=0.9,
                frequency_penalty=0.5,
                presence_penalty=0.5,
            )

            litellm_utils.executor.shutdown(wait=True)

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 1
    span = chat_spans[0]

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.5
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.5


@pytest.mark.asyncio(loop_scope="session")
async def test_async_additional_parameters(
    sentry_init,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
):
    """Test that additional parameters are captured."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]
    client = AsyncOpenAI(api_key="test-key")

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
                temperature=0.7,
                max_tokens=100,
                top_p=0.9,
                frequency_penalty=0.5,
                presence_penalty=0.5,
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 1
    span = chat_spans[0]

    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.5
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.5


def test_no_integration(
    reset_litellm_executor,
    sentry_init,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
):
    """Test that when integration is not enabled, callbacks don't break."""
    sentry_init(
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]
    client = OpenAI(api_key="test-key")

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
            )

            litellm_utils.executor.shutdown(wait=True)

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_async_no_integration(
    sentry_init,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
):
    """Test that when integration is not enabled, callbacks don't break."""
    sentry_init(
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction", "span")

    messages = [{"role": "user", "content": "Hello!"}]
    client = AsyncOpenAI(api_key="test-key")

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response,
        serialize_pydantic=True,
        request_headers={"X-Stainless-Raw-Response": "true"},
    )

    with mock.patch.object(
        client.completions._client._client,
        "send",
        return_value=model_response,
    ):
        with start_transaction(name="litellm test"):
            await litellm.acompletion(
                model="gpt-3.5-turbo",
                messages=messages,
                client=client,
            )

            await GLOBAL_LOGGING_WORKER.flush()
            await asyncio.sleep(0.5)

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = list(
        x
        for x in spans
        if x["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
        and x["attributes"]["sentry.origin"] == "auto.ai.litellm"
    )
    assert len(chat_spans) == 0


def test_response_without_usage(sentry_init, capture_items):
    """Test handling of responses without usage information."""
    sentry_init(
        integrations=[LiteLLMIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("transaction", "span")

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

    (span,) = (item.payload for item in items if item.type == "span")

    # Span should still be created even without usage info
    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat gpt-3.5-turbo"


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


def test_litellm_message_truncation(sentry_init, capture_items):
    """Test that large messages are truncated properly in LiteLLM integration."""
    sentry_init(
        integrations=[LiteLLMIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    items = capture_items("transaction", "span")

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )
    messages = [
        {"role": "user", "content": "small message 1"},
        {"role": "assistant", "content": large_content},
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": "small message 4"},
        {"role": "user", "content": "small message 5"},
    ]
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

    spans = [item.payload for item in items if item.type == "span"]
    chat_spans = [
        span for span in spans if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
    ]
    assert len(chat_spans) > 0

    chat_span = chat_spans[0]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in chat_span["attributes"]

    messages_data = chat_span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) == 1
    assert "small message 5" in str(parsed_messages[0])

    tx = next(item.payload for item in items if item.type == "transaction")
    assert tx["_meta"]["spans"]["0"]["data"]["gen_ai.request.messages"][""]["len"] == 5

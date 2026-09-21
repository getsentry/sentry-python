from unittest import mock

import pytest
from mistralai.client import Mistral
from mistralai.client.chat import Chat
from mistralai.client.models import (
    AssistantMessage,
    ChatCompletionChoice,
    ChatCompletionResponse,
    UsageInfo,
)

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.mistral import MistralIntegration


@pytest.fixture
def mistral_response():
    return ChatCompletionResponse(
        id="chat-id",
        object="chat.completion",
        model="mistral-medium-3-5",
        created=10000000,
        usage=UsageInfo(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        ),
        choices=[
            ChatCompletionChoice(
                index=0,
                finish_reason="stop",
                message=AssistantMessage(content="Hello, how can I help you?"),
            )
        ],
    )


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_nonstreaming_chat(
    sentry_init,
    capture_items,
    get_model_response,
    mistral_response,
    stream_gen_ai_spans,
    span_streaming,
):
    sentry_init(
        integrations=[MistralIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    client = Mistral(api_key="z")

    model_response = get_model_response(
        mistral_response,
        serialize_pydantic=True,
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with mock.patch.object(
            Chat,
            "do_request",
            return_value=model_response,
        ), sentry_sdk.start_transaction(name="mistral"):
            client.chat.complete(
                model="mistral-medium-latest",
                messages=[
                    {"role": "user", "content": "What is the best French cheese?"}
                ],
                max_tokens=1024,
                presence_penalty=0.1,
                frequency_penalty=0.2,
                temperature=0.7,
                top_p=0.9,
                reasoning_effort="high",
            )
        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["name"] == "chat mistral-medium-latest"
        assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "mistral"
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "mistral-medium-latest"
        )
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    else:
        items = capture_items("transaction")

        with mock.patch.object(
            Chat,
            "do_request",
            return_value=model_response,
        ), sentry_sdk.start_transaction(name="mistral"):
            client.chat.complete(
                model="open-mistral",
                messages=[{"role": "user", "content": "Hello, Mistral"}],
                max_tokens=1024,
                presence_penalty=0.1,
                frequency_penalty=0.2,
                temperature=0.7,
                top_p=0.9,
                reasoning_effort="high",
            )

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]

        assert span["description"] == "chat open-mistral"
        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "mistral"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "open-mistral"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["data"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
        assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
async def test_nonstreaming_chat_async(
    sentry_init,
    capture_items,
    get_model_response,
    mistral_response,
    stream_gen_ai_spans,
    span_streaming,
):
    sentry_init(
        integrations=[MistralIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    client = Mistral(api_key="z")

    model_response = get_model_response(
        mistral_response,
        serialize_pydantic=True,
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with mock.patch.object(
            Chat,
            "do_request_async",
            return_value=model_response,
        ), sentry_sdk.start_transaction(name="mistral"):
            await client.chat.complete_async(
                model="mistral-medium-latest",
                messages=[
                    {"role": "user", "content": "What is the best French cheese?"}
                ],
                max_tokens=1024,
                presence_penalty=0.1,
                frequency_penalty=0.2,
                temperature=0.7,
                top_p=0.9,
                reasoning_effort="high",
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["name"] == "chat mistral-medium-latest"
        assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "mistral"
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "mistral-medium-latest"
        )
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    else:
        items = capture_items("transaction")

        with mock.patch.object(
            Chat,
            "do_request_async",
            return_value=model_response,
        ), sentry_sdk.start_transaction(name="mistral"):
            await client.chat.complete_async(
                model="mistral-medium-latest",
                messages=[{"role": "user", "content": "Hello, Mistral"}],
                max_tokens=1024,
                presence_penalty=0.1,
                frequency_penalty=0.2,
                temperature=0.7,
                top_p=0.9,
                reasoning_effort="high",
            )

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]

        assert span["description"] == "chat mistral-medium-latest"
        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "mistral"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "mistral-medium-latest"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
        assert span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
        assert span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["data"][SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL] == "high"

        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
        assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30

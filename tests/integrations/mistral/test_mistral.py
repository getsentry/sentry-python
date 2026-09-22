import json
from unittest import mock

import pytest
from mistralai.client import Mistral
from mistralai.client.chat import Chat
from mistralai.client.models import (
    AssistantMessage,
    ChatCompletionChoice,
    ChatCompletionResponse,
    SystemMessage,
    TextChunk,
    UsageInfo,
    UserMessage,
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
            ),
            ChatCompletionChoice(
                index=1,
                finish_reason="stop",
                message=AssistantMessage(
                    content=[
                        TextChunk(text="Response 1"),
                        TextChunk(text="Response 2"),
                    ]
                ),
            ),
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


@pytest.mark.parametrize(
    "messages,expected_system_instructions,expected_input_messages",
    (
        (
            [
                SystemMessage(
                    content="You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning."
                ),
                UserMessage(content="How can I solve 8x + 7 = -23"),
                AssistantMessage(
                    content="Subtract 7 from both sides to isolate the term with x ..."
                ),
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning.",
                }
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "How can I solve 8x + 7 = -23",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Subtract 7 from both sides to isolate the term with x ...",
                        }
                    ],
                },
            ],
        ),
        (
            [
                SystemMessage(
                    content=[
                        TextChunk(text="You are a helpful assistant."),
                        TextChunk(text="Be concise and clear."),
                    ]
                ),
                UserMessage(
                    content=[
                        TextChunk(text="What is the best French cheese?"),
                        TextChunk(text="give the best 50"),
                    ]
                ),
                AssistantMessage(
                    content=[
                        TextChunk(text="Camembert de Normandie"),
                        TextChunk(text="Brie de Meaux"),
                    ]
                ),
            ],
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "What is the best French cheese?",
                        },
                        {
                            "type": "text",
                            "content": "give the best 50",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Camembert de Normandie",
                        },
                        {
                            "type": "text",
                            "content": "Brie de Meaux",
                        },
                    ],
                },
            ],
        ),
        (
            [
                {
                    "role": "system",
                    "content": "You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning.",
                },
                {"role": "user", "content": "How can I solve 8x + 7 = -23"},
                {
                    "role": "assistant",
                    "content": "Subtract 7 from both sides to isolate the term with x ...",
                },
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning.",
                }
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "How can I solve 8x + 7 = -23",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Subtract 7 from both sides to isolate the term with x ...",
                        }
                    ],
                },
            ],
        ),
        (
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is the best French cheese?"},
                        {"type": "text", "text": "give the best 50"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Camembert de Normandie"},
                        {"type": "text", "text": "Brie de Meaux"},
                    ],
                },
            ],
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "What is the best French cheese?",
                        },
                        {
                            "type": "text",
                            "content": "give the best 50",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Camembert de Normandie",
                        },
                        {
                            "type": "text",
                            "content": "Brie de Meaux",
                        },
                    ],
                },
            ],
        ),
    ),
)
@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_input_attributes_nonstreaming_chat(
    sentry_init,
    capture_items,
    get_model_response,
    mistral_response,
    messages,
    expected_system_instructions,
    expected_input_messages,
    data_collection,
    stream_gen_ai_spans,
    span_streaming,
):
    if data_collection:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            data_collection={},
        )
    else:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            send_default_pii=True,
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
                messages=messages,
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["name"] == "chat mistral-medium-latest"
        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
            == expected_system_instructions
        )
        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )
    else:
        items = capture_items("transaction")

        with mock.patch.object(
            Chat,
            "do_request",
            return_value=model_response,
        ), sentry_sdk.start_transaction(name="mistral"):
            client.chat.complete(
                model="open-mistral",
                messages=messages,
            )

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]

        assert span["description"] == "chat open-mistral"
        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
            == expected_system_instructions
        )
        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )


@pytest.mark.parametrize(
    "messages,expected_system_instructions,expected_input_messages",
    (
        (
            [
                SystemMessage(
                    content="You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning."
                ),
                UserMessage(content="How can I solve 8x + 7 = -23"),
                AssistantMessage(
                    content="Subtract 7 from both sides to isolate the term with x ..."
                ),
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning.",
                }
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "How can I solve 8x + 7 = -23",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Subtract 7 from both sides to isolate the term with x ...",
                        }
                    ],
                },
            ],
        ),
        (
            [
                SystemMessage(
                    content=[
                        TextChunk(text="You are a helpful assistant."),
                        TextChunk(text="Be concise and clear."),
                    ]
                ),
                UserMessage(
                    content=[
                        TextChunk(text="What is the best French cheese?"),
                        TextChunk(text="give the best 50"),
                    ]
                ),
                AssistantMessage(
                    content=[
                        TextChunk(text="Camembert de Normandie"),
                        TextChunk(text="Brie de Meaux"),
                    ]
                ),
            ],
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "What is the best French cheese?",
                        },
                        {
                            "type": "text",
                            "content": "give the best 50",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Camembert de Normandie",
                        },
                        {
                            "type": "text",
                            "content": "Brie de Meaux",
                        },
                    ],
                },
            ],
        ),
        (
            [
                {
                    "role": "system",
                    "content": "You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning.",
                },
                {"role": "user", "content": "How can I solve 8x + 7 = -23"},
                {
                    "role": "assistant",
                    "content": "Subtract 7 from both sides to isolate the term with x ...",
                },
            ],
            [
                {
                    "type": "text",
                    "content": "You are a helpful math tutor. You will be provided with a math problem, and your goal will be to output a step by step solution, along with a final answer. For each step, just provide the output as an equation use the explanation field to detail the reasoning.",
                }
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "How can I solve 8x + 7 = -23",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Subtract 7 from both sides to isolate the term with x ...",
                        }
                    ],
                },
            ],
        ),
        (
            [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "You are a helpful assistant."},
                        {"type": "text", "text": "Be concise and clear."},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is the best French cheese?"},
                        {"type": "text", "text": "give the best 50"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Camembert de Normandie"},
                        {"type": "text", "text": "Brie de Meaux"},
                    ],
                },
            ],
            [
                {"type": "text", "content": "You are a helpful assistant."},
                {"type": "text", "content": "Be concise and clear."},
            ],
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "content": "What is the best French cheese?",
                        },
                        {
                            "type": "text",
                            "content": "give the best 50",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "text",
                            "content": "Camembert de Normandie",
                        },
                        {
                            "type": "text",
                            "content": "Brie de Meaux",
                        },
                    ],
                },
            ],
        ),
    ),
)
@pytest.mark.asyncio
@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
async def test_input_attributes_nonstreaming_chat_async(
    sentry_init,
    capture_items,
    get_model_response,
    mistral_response,
    messages,
    expected_system_instructions,
    expected_input_messages,
    data_collection,
    stream_gen_ai_spans,
    span_streaming,
):
    if data_collection:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            data_collection={},
        )
    else:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            send_default_pii=True,
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
                messages=messages,
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["name"] == "chat mistral-medium-latest"
        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
            == expected_system_instructions
        )
        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )
    else:
        items = capture_items("transaction")

        with mock.patch.object(
            Chat,
            "do_request_async",
            return_value=model_response,
        ), sentry_sdk.start_transaction(name="mistral"):
            await client.chat.complete_async(
                model="mistral-medium-latest",
                messages=messages,
            )

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]

        assert span["description"] == "chat mistral-medium-latest"
        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
            == expected_system_instructions
        )
        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )


@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_output_attributes_nonstreaming_chat(
    sentry_init,
    capture_items,
    get_model_response,
    mistral_response,
    data_collection,
    stream_gen_ai_spans,
    span_streaming,
):
    if data_collection:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            data_collection={},
        )
    else:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            send_default_pii=True,
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
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["name"] == "chat mistral-medium-latest"
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Hello, how can I help you?"},
                ],
            },
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Response 1"},
                    {"type": "text", "content": "Response 2"},
                ],
            },
        ]
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
            )

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]

        assert span["description"] == "chat open-mistral"
        assert json.loads(span["data"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Hello, how can I help you?"},
                ],
            },
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Response 1"},
                    {"type": "text", "content": "Response 2"},
                ],
            },
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
async def test_output_attributes_nonstreaming_chat_async(
    sentry_init,
    capture_items,
    get_model_response,
    mistral_response,
    data_collection,
    stream_gen_ai_spans,
    span_streaming,
):
    if data_collection:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            data_collection={},
        )
    else:
        sentry_init(
            integrations=[MistralIntegration()],
            traces_sample_rate=1.0,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
            send_default_pii=True,
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
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["name"] == "chat mistral-medium-latest"
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Hello, how can I help you?"},
                ],
            },
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Response 1"},
                    {"type": "text", "content": "Response 2"},
                ],
            },
        ]
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
            )

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]

        assert span["description"] == "chat mistral-medium-latest"
        assert json.loads(span["data"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Hello, how can I help you?"},
                ],
            },
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Response 1"},
                    {"type": "text", "content": "Response 2"},
                ],
            },
        ]

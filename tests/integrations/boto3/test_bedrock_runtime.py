import base64
import binascii
import copy
import json
import struct
from io import BytesIO

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError, EventStreamError
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.ai.utils import MAX_SINGLE_MESSAGE_CONTENT_CHARS
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3.consts import AI_ORIGIN, ORIGIN
from tests.integrations.boto3.aws_mock import Body, MockResponse

session = boto3.Session(  # type: ignore[attr-defined]
    aws_access_key_id="-",
    aws_secret_access_key="-",
    region_name="us-east-1",
)

if "bedrock-runtime" not in session.get_available_services() or not hasattr(
    session.client("bedrock-runtime"), "converse"
):
    pytest.skip(
        "botocore doesn't support the Bedrock Runtime Converse API",
        allow_module_level=True,
    )


def _has_shape_member(shape_name, member_name):
    service_model = session.client("bedrock-runtime").meta.service_model
    return member_name in service_model.shape_for(shape_name).members


MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
# as long as the last input message's text can be without being truncated.
LONG_TEXT = "a" * MAX_SINGLE_MESSAGE_CONTENT_CHARS
HUGE_TEXT = "a" * (MAX_SINGLE_MESSAGE_CONTENT_CHARS * 30)
TRUNCATED_TEXT = LONG_TEXT + "..."

CONVERSE_RESPONSE = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [{"text": "Hello! How can I help?"}],
        }
    },
    "stopReason": "end_turn",
    "usage": {"inputTokens": 12, "outputTokens": 8, "totalTokens": 20},
    "metrics": {"latencyMs": 100},
}

TRACE_MODES = [
    pytest.param({"trace_lifecycle": "stream"}, id="span_streaming"),
    pytest.param({"trace_lifecycle": "static"}, id="static"),
    pytest.param(
        {"trace_lifecycle": "static", "stream_gen_ai_spans": False},
        id="static_without_gen_ai_span_streaming",
    ),
]

# `sentry_init()` options, `include_prompts`, and whether prompts (inputs) and
# responses (outputs) are recorded.
GEN_AI_DATA_COLLECTION_CASES = [
    pytest.param({}, True, False, False, id="default"),
    pytest.param({"send_default_pii": True}, True, True, True, id="send_default_pii"),
    pytest.param(
        {"send_default_pii": True},
        False,
        False,
        False,
        id="send_default_pii_without_include_prompts",
    ),
    pytest.param({"data_collection": {}}, True, True, True, id="data_collection"),
    pytest.param(
        {"data_collection": {"gen_ai": {"inputs": True, "outputs": False}}},
        True,
        True,
        False,
        id="data_collection_inputs_only",
    ),
    pytest.param(
        {"data_collection": {"gen_ai": {"inputs": False, "outputs": True}}},
        True,
        False,
        True,
        id="data_collection_outputs_only",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "data_collection": {"gen_ai": {"inputs": False, "outputs": False}},
        },
        True,
        False,
        False,
        id="data_collection_wins_over_send_default_pii",
    ),
    pytest.param(
        {"data_collection": {"gen_ai": {"inputs": True, "outputs": True}}},
        False,
        True,
        True,
        id="data_collection_ignores_include_prompts",
    ),
]


@pytest.fixture
def init_bedrock(sentry_init):
    def inner(trace_mode, include_prompts=True, **kwargs):
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration(include_prompts=include_prompts)],
            server_name="",
            **trace_mode,
            **kwargs,
        )
        return session.client("bedrock-runtime")

    return inner


def _capture_spans(capture_items, trace_mode, call):
    """
    Return the finished spans, normalized to the span streaming format, i.e.
    with the span's data and `sentry.op` and `sentry.origin` in `attributes`.
    """
    items = capture_items("transaction", "span")

    if trace_mode["trace_lifecycle"] == "stream":
        with sentry_sdk.traces.start_span(name="parent"):  # type: ignore[attr-defined]
            call()
    else:
        with sentry_sdk.start_transaction(name="parent"):
            call()
    sentry_sdk.flush()

    spans = []
    for item in items:
        if item.type == "span":
            spans.append(item.payload)
            continue

        for span in item.payload["spans"]:
            assert span["timestamp"] is not None
            spans.append(
                {
                    "name": span["description"],
                    # like the conversion of `gen_ai` spans to the span streaming format.
                    "status": "ok" if span.get("status") in (None, "ok") else "error",
                    "attributes": {
                        SPANDATA.SENTRY_OP: span["op"],
                        SPANDATA.SENTRY_ORIGIN: span["origin"],
                        **span["data"],
                    },
                }
            )

    return spans


def _expected_input_messages(trace_mode, input_messages):
    """
    Like the other `gen_ai` integrations, only the last input message is kept when
    `gen_ai` spans are sent in transactions.
    """
    if trace_mode.get("stream_gen_ai_spans") is False:
        return input_messages[-1:]
    return input_messages


def _expected_gen_ai_attributes(trace_mode, attributes):
    if SPANDATA.GEN_AI_INPUT_MESSAGES not in attributes:
        return attributes

    return {
        **attributes,
        SPANDATA.GEN_AI_INPUT_MESSAGES: _expected_input_messages(
            trace_mode, attributes[SPANDATA.GEN_AI_INPUT_MESSAGES]
        ),
    }


def _get_bedrock_span(spans, origin=AI_ORIGIN):
    (span,) = (
        span
        for span in spans
        if span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == origin
    )
    return span


# The last Bedrock message, how it's recorded, and how it's recorded when it's
# truncated. Its text, tool call arguments, and tool results share one limit, and
# JSON that doesn't fit is replaced by its truncated serialization.
LAST_MESSAGE_CASES = [
    pytest.param(
        {"role": "user", "content": [{"text": "Hello!"}, {"text": LONG_TEXT}]},
        {
            "role": "user",
            "parts": [
                {"type": "text", "content": "Hello!"},
                {"type": "text", "content": LONG_TEXT},
            ],
        },
        {
            "role": "user",
            "parts": [
                {"type": "text", "content": "Hello!"},
                {
                    "type": "text",
                    "content": LONG_TEXT[: MAX_SINGLE_MESSAGE_CONTENT_CHARS - 6]
                    + "...",
                },
            ],
        },
        id="text",
    ),
    pytest.param(
        {"role": "user", "content": [{"text": LONG_TEXT}]},
        {"role": "user", "parts": [{"type": "text", "content": LONG_TEXT}]},
        {"role": "user", "parts": [{"type": "text", "content": LONG_TEXT}]},
        id="text_at_limit",
    ),
    pytest.param(
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "tool-1",
                        "content": [{"text": HUGE_TEXT}],
                    }
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {"type": "tool_call_response", "id": "tool-1", "result": HUGE_TEXT}
            ],
        },
        {
            "role": "tool",
            "parts": [
                {"type": "tool_call_response", "id": "tool-1", "result": TRUNCATED_TEXT}
            ],
        },
        id="tool_result",
    ),
    pytest.param(
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "tool-1",
                        "content": [
                            {"json": {"weather": "rainy"}},
                            {"text": HUGE_TEXT},
                            {"text": "Done."},
                        ],
                    }
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_call_response",
                    "id": "tool-1",
                    "result": [{"weather": "rainy"}, HUGE_TEXT, "Done."],
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_call_response",
                    "id": "tool-1",
                    # the JSON item counts with its serialized length.
                    "result": [
                        {"weather": "rainy"},
                        HUGE_TEXT[
                            : MAX_SINGLE_MESSAGE_CONTENT_CHARS
                            - len(json.dumps({"weather": "rainy"}))
                        ]
                        + "...",
                        "...",
                    ],
                }
            ],
        },
        id="tool_result_list",
    ),
    pytest.param(
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "tool-1",
                        "name": "get_weather",
                        "input": HUGE_TEXT,
                    }
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "id": "tool-1",
                    "name": "get_weather",
                    "arguments": HUGE_TEXT,
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "id": "tool-1",
                    "name": "get_weather",
                    "arguments": TRUNCATED_TEXT,
                }
            ],
        },
        id="tool_call_arguments",
    ),
    pytest.param(
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "tool-1",
                        "content": [{"json": {"blob": HUGE_TEXT}}],
                    }
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_call_response",
                    "id": "tool-1",
                    "result": {"blob": HUGE_TEXT},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_call_response",
                    "id": "tool-1",
                    "result": json.dumps({"blob": HUGE_TEXT})[
                        :MAX_SINGLE_MESSAGE_CONTENT_CHARS
                    ]
                    + "...",
                }
            ],
        },
        id="tool_result_json",
    ),
    pytest.param(
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "tool-1",
                        "name": "get_weather",
                        "input": {"city": HUGE_TEXT},
                    }
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "id": "tool-1",
                    "name": "get_weather",
                    "arguments": {"city": HUGE_TEXT},
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "id": "tool-1",
                    "name": "get_weather",
                    "arguments": json.dumps({"city": HUGE_TEXT})[
                        :MAX_SINGLE_MESSAGE_CONTENT_CHARS
                    ]
                    + "...",
                }
            ],
        },
        id="tool_call_arguments_json",
    ),
]


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "init_kwargs, include_prompts, record_inputs, record_outputs",
    GEN_AI_DATA_COLLECTION_CASES,
)
@pytest.mark.parametrize(
    "last_message, expected_last_message, expected_truncated_last_message",
    LAST_MESSAGE_CASES,
)
def test_converse(
    init_bedrock,
    capture_items,
    trace_mode,
    init_kwargs,
    include_prompts,
    record_inputs,
    record_outputs,
    last_message,
    expected_last_message,
    expected_truncated_last_message,
):
    client = init_bedrock(trace_mode, include_prompts=include_prompts, **init_kwargs)
    params = {
        "modelId": MODEL_ID,
        "system": [{"text": "You are a helpful assistant."}],
        "messages": [
            {"role": "user", "content": [{"text": "Hi!"}]},
            {"role": "assistant", "content": [{"text": "Hi! What do you need?"}]},
            last_message,
        ],
        "inferenceConfig": {
            "maxTokens": 100,
            "temperature": 0.5,
            "topP": 0.9,
            "stopSequences": ["STOP"],
        },
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": "get_weather",
                        "description": "Get the weather for a city.",
                        "inputSchema": {
                            "json": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            }
                        },
                    }
                }
            ]
        },
    }

    original_params = copy.deepcopy(params)

    with Stubber(client) as stubber:
        stubber.add_response("converse", CONVERSE_RESPONSE, original_params)
        responses = []
        spans = _capture_spans(
            capture_items,
            trace_mode,
            lambda: responses.append(client.converse(**params)),
        )

    assert responses[0]["output"] == CONVERSE_RESPONSE["output"]
    # the request isn't changed by the truncation.
    assert params == original_params

    span = _get_bedrock_span(spans)
    attributes = span["attributes"]
    assert span["name"] == f"chat {MODEL_ID}"
    assert span["status"] == "ok"
    assert attributes[SPANDATA.SENTRY_OP] == OP.GEN_AI_CHAT
    assert attributes[SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
    assert attributes[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert attributes[SPANDATA.GEN_AI_REQUEST_MODEL] == MODEL_ID
    assert attributes[SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
    assert attributes[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert attributes[SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.5
    assert attributes[SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    assert attributes[SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES] == ["STOP"]
    assert attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["end_turn"]
    assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 12
    assert attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 8
    assert attributes[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS not in attributes
    # the generic AWS client attributes are kept.
    assert attributes[SPANDATA.RPC_METHOD] == "Converse"
    assert attributes[SPANDATA.CLOUD_REGION] == "us-east-1"

    if record_inputs:
        assert json.loads(attributes[SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
            {"type": "text", "content": "You are a helpful assistant."}
        ]
        if trace_mode.get("stream_gen_ai_spans") is False:
            expected_input_messages = [expected_truncated_last_message]
        else:
            expected_input_messages = [
                {"role": "user", "parts": [{"type": "text", "content": "Hi!"}]},
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": "Hi! What do you need?"}],
                },
                expected_last_message,
            ]
        assert (
            json.loads(attributes[SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )
        assert json.loads(attributes[SPANDATA.GEN_AI_TOOL_DEFINITIONS]) == [
            {
                "type": "function",
                "name": "get_weather",
                "description": "Get the weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            }
        ]
    else:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in attributes
        assert SPANDATA.GEN_AI_INPUT_MESSAGES not in attributes
        assert SPANDATA.GEN_AI_TOOL_DEFINITIONS not in attributes

    if record_outputs:
        assert json.loads(attributes[SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "role": "assistant",
                "parts": [{"type": "text", "content": "Hello! How can I help?"}],
            }
        ]
    else:
        assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in attributes


@pytest.mark.skipif(
    not _has_shape_member("ContentBlock", "reasoningContent"),
    reason="botocore doesn't support reasoning content",
)
@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_converse_tool_use_and_reasoning(init_bedrock, capture_items, trace_mode):
    client = init_bedrock(trace_mode, send_default_pii=True)
    params = {
        "modelId": MODEL_ID,
        "messages": [
            {"role": "user", "content": [{"text": "Weather in Paris?"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "reasoningContent": {
                            "reasoningText": {"text": "I need the weather tool."}
                        }
                    },
                    {
                        "toolUse": {
                            "toolUseId": "tool-1",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        }
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "tool-1",
                            "content": [{"json": {"weather": "rainy"}}],
                        }
                    },
                    {"text": "And in London?"},
                    {"image": {"format": "png", "source": {"bytes": b"\x89PNG"}}},
                ],
            },
        ],
    }
    response = {
        **CONVERSE_RESPONSE,
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Let me check."},
                    {
                        "toolUse": {
                            "toolUseId": "tool-2",
                            "name": "get_weather",
                            "input": {"city": "London"},
                        }
                    },
                ],
            }
        },
        "stopReason": "tool_use",
    }

    with Stubber(client) as stubber:
        stubber.add_response("converse", response, params)
        spans = _capture_spans(
            capture_items, trace_mode, lambda: client.converse(**params)
        )

    attributes = _get_bedrock_span(spans)["attributes"]
    expected_input_messages = [
        {"role": "user", "parts": [{"type": "text", "content": "Weather in Paris?"}]},
        {
            "role": "assistant",
            "parts": [
                {"type": "reasoning", "content": "I need the weather tool."},
                {
                    "type": "tool_call",
                    "id": "tool-1",
                    "name": "get_weather",
                    "arguments": {"city": "Paris"},
                },
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_call_response",
                    "id": "tool-1",
                    "result": {"weather": "rainy"},
                }
            ],
        },
        # media content isn't recorded.
        {"role": "user", "parts": [{"type": "text", "content": "And in London?"}]},
    ]
    assert json.loads(
        attributes[SPANDATA.GEN_AI_INPUT_MESSAGES]
    ) == _expected_input_messages(trace_mode, expected_input_messages)
    assert json.loads(attributes[SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
        {
            "role": "assistant",
            "parts": [
                {"type": "text", "content": "Let me check."},
                {
                    "type": "tool_call",
                    "id": "tool-2",
                    "name": "get_weather",
                    "arguments": {"city": "London"},
                },
            ],
        }
    ]
    assert attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["tool_use"]


@pytest.mark.skipif(
    not _has_shape_member("TokenUsage", "cacheReadInputTokens"),
    reason="botocore doesn't support prompt caching",
)
@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_converse_prompt_cache_usage(init_bedrock, capture_items, trace_mode):
    client = init_bedrock(trace_mode)
    params = {
        "modelId": MODEL_ID,
        "messages": [{"role": "user", "content": [{"text": "Hello!"}]}],
    }
    response = {
        **CONVERSE_RESPONSE,
        "usage": {
            "inputTokens": 4,
            "outputTokens": 8,
            "totalTokens": 1662,
            "cacheReadInputTokens": 1000,
            "cacheWriteInputTokens": 650,
        },
    }

    with Stubber(client) as stubber:
        stubber.add_response("converse", response, params)
        spans = _capture_spans(
            capture_items, trace_mode, lambda: client.converse(**params)
        )

    attributes = _get_bedrock_span(spans)["attributes"]
    # Bedrock's `inputTokens` excludes cached tokens; `gen_ai.usage.input_tokens` doesn't.
    assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 1654
    assert attributes[SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS] == 1000
    assert attributes[SPANDATA.GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS] == 650
    assert attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 8
    assert attributes[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 1662


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_converse_error_is_unchanged(init_bedrock, capture_items, trace_mode):
    client = init_bedrock(trace_mode)
    params = {
        "modelId": MODEL_ID,
        "messages": [{"role": "user", "content": [{"text": "Hello!"}]}],
    }
    errors = []

    def call():
        with pytest.raises(ClientError) as exc_info:
            client.converse(**params)
        errors.append(exc_info.value)

    with Stubber(client) as stubber:
        stubber.add_client_error(
            "converse",
            service_error_code="ThrottlingException",
            http_status_code=429,
            expected_params=params,
        )
        spans = _capture_spans(capture_items, trace_mode, call)

    assert errors[0].response["Error"]["Code"] == "ThrottlingException"

    span = _get_bedrock_span(spans)
    attributes = span["attributes"]
    assert span["name"] == f"chat {MODEL_ID}"
    assert span["status"] == "error"
    assert attributes[SPANDATA.SENTRY_OP] == OP.GEN_AI_CHAT
    assert attributes[SPANDATA.GEN_AI_REQUEST_MODEL] == MODEL_ID
    assert attributes[SPANDATA.ERROR_TYPE] == "ThrottlingException"
    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 429
    assert SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS not in attributes


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_other_operations_keep_generic_span(init_bedrock, capture_items, trace_mode):
    client = init_bedrock(trace_mode, send_default_pii=True)

    with Stubber(client) as stubber:
        stubber.add_response("list_async_invokes", {"asyncInvokeSummaries": []}, {})
        spans = _capture_spans(
            capture_items, trace_mode, lambda: client.list_async_invokes()
        )

    span = _get_bedrock_span(spans, origin=ORIGIN)
    assert span["name"] == "aws.bedrock-runtime.ListAsyncInvokes"
    assert span["attributes"][SPANDATA.SENTRY_OP] == OP.HTTP_CLIENT
    assert not any(key.startswith("gen_ai.") for key in span["attributes"])


# base64-encoded media content, which isn't recorded.
MEDIA_BASE64 = base64.b64encode(b"\x89PNG media bytes").decode()
ANTHROPIC_MEDIA_SOURCE = {
    "type": "base64",
    "media_type": "image/png",
    "data": MEDIA_BASE64,
}

INVOKE_MODEL_CASES = [
    pytest.param(
        "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 100,
            "temperature": 0.5,
            "top_p": 0.9,
            "stop_sequences": ["STOP"],
            "system": "Be brief.",
            "messages": [
                {"role": "user", "content": "Weather in Paris?"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "I need the tool."},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "rainy",
                        }
                    ],
                },
            ],
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get the weather for a city.",
                    "input_schema": {"type": "object"},
                }
            ],
        },
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi!"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 50,
            },
        },
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE: 0.5,
            SPANDATA.GEN_AI_REQUEST_TOP_P: 0.9,
            SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES: ["STOP"],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["end_turn"],
            # Anthropic's `input_tokens` excludes cached tokens.
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 160,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 100,
            SPANDATA.GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS: 50,
            SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: [
                {"type": "text", "content": "Be brief."}
            ],
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {
                    "role": "user",
                    "parts": [{"type": "text", "content": "Weather in Paris?"}],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "reasoning", "content": "I need the tool."},
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "get_weather",
                            "arguments": {"city": "Paris"},
                        },
                    ],
                },
                {
                    "role": "tool",
                    "parts": [
                        {
                            "type": "tool_call_response",
                            "id": "tool-1",
                            "result": "rainy",
                        }
                    ],
                },
            ],
            SPANDATA.GEN_AI_TOOL_DEFINITIONS: [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get the weather for a city.",
                    "parameters": {"type": "object"},
                }
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="anthropic_claude",
    ),
    pytest.param(
        "anthropic.claude-3-5-sonnet-20240620-v1:0",
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 100,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in these?"},
                        {"type": "image", "source": ANTHROPIC_MEDIA_SOURCE},
                        {
                            "type": "document",
                            "source": {
                                **ANTHROPIC_MEDIA_SOURCE,
                                "media_type": "application/pdf",
                            },
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "screenshot",
                            "input": {},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": [
                                {"type": "text", "text": "Done."},
                                {"type": "image", "source": ANTHROPIC_MEDIA_SOURCE},
                            ],
                        }
                    ],
                },
            ],
        },
        {
            "id": "msg_2",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "A cat."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["end_turn"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {
                    "role": "user",
                    "parts": [{"type": "text", "content": "What's in these?"}],
                },
                {
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "screenshot",
                            "arguments": {},
                        },
                    ],
                },
                {
                    "role": "tool",
                    "parts": [
                        {
                            "type": "tool_call_response",
                            "id": "tool-1",
                            "result": "Done.",
                        }
                    ],
                },
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": "A cat."}],
                }
            ],
        },
        id="anthropic_claude_media",
    ),
    pytest.param(
        "amazon.nova-lite-v1:0",
        {
            "schemaVersion": "messages-v1",
            "system": [{"text": "Be brief."}],
            "messages": [{"role": "user", "content": [{"text": "Hello!"}]}],
            "inferenceConfig": {
                "max_new_tokens": 100,
                "temperature": 0.5,
                "top_p": 0.9,
                "stopSequences": ["STOP"],
            },
        },
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "Hi!"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        },
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE: 0.5,
            SPANDATA.GEN_AI_REQUEST_TOP_P: 0.9,
            SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES: ["STOP"],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["end_turn"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS: 15,
            SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: [
                {"type": "text", "content": "Be brief."}
            ],
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="amazon_nova",
    ),
    pytest.param(
        "amazon.nova-lite-v1:0",
        {
            "schemaVersion": "messages-v1",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": "What's this?"},
                        {"image": {"format": "png", "source": {"bytes": MEDIA_BASE64}}},
                    ],
                }
            ],
            "inferenceConfig": {"max_new_tokens": 100},
        },
        {
            "output": {
                "message": {"role": "assistant", "content": [{"text": "A cat."}]}
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        },
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["end_turn"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS: 15,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "What's this?"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": "A cat."}],
                }
            ],
        },
        id="amazon_nova_media",
    ),
    pytest.param(
        "amazon.titan-text-express-v1",
        {
            "inputText": "Hello!",
            "textGenerationConfig": {
                "maxTokenCount": 100,
                "temperature": 0.5,
                "topP": 0.9,
                "stopSequences": ["User:"],
            },
        },
        {
            "inputTextTokenCount": 10,
            "results": [
                {"tokenCount": 5, "outputText": "Hi!", "completionReason": "FINISH"}
            ],
        },
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE: 0.5,
            SPANDATA.GEN_AI_REQUEST_TOP_P: 0.9,
            SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES: ["User:"],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["FINISH"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="amazon_titan",
    ),
    pytest.param(
        "meta.llama3-8b-instruct-v1:0",
        {"prompt": "Hello!", "max_gen_len": 100, "temperature": 0.5, "top_p": 0.9},
        {
            "generation": "Hi!",
            "prompt_token_count": 10,
            "generation_token_count": 5,
            "stop_reason": "stop",
        },
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE: 0.5,
            SPANDATA.GEN_AI_REQUEST_TOP_P: 0.9,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["stop"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="meta_llama",
    ),
    pytest.param(
        "cohere.command-r-v1:0",
        {
            "message": "Hello!",
            "preamble": "Be brief.",
            "max_tokens": 100,
            "temperature": 0.5,
            "p": 0.9,
            "stop_sequences": ["STOP"],
        },
        {"response_id": "1", "text": "Hi!", "finish_reason": "COMPLETE"},
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE: 0.5,
            SPANDATA.GEN_AI_REQUEST_TOP_P: 0.9,
            SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES: ["STOP"],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["COMPLETE"],
            # the response body has no usage; it's in the response headers.
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
            SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: [
                {"type": "text", "content": "Be brief."}
            ],
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="cohere_command_r",
    ),
    pytest.param(
        "cohere.command-text-v14",
        {
            "prompt": "Hello!",
            "max_tokens": 100,
            "temperature": 0.5,
            "p": 0.9,
            "stop_sequences": ["STOP"],
        },
        {
            "id": "1",
            "prompt": "Hello!",
            "generations": [{"id": "1", "text": "Hi!", "finish_reason": "COMPLETE"}],
        },
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE: 0.5,
            SPANDATA.GEN_AI_REQUEST_TOP_P: 0.9,
            SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES: ["STOP"],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["COMPLETE"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="cohere_command",
    ),
    pytest.param(
        "mistral.mistral-7b-instruct-v0:2",
        {
            "prompt": "<s>[INST] Hello! [/INST]",
            "max_tokens": 100,
            "temperature": 0.5,
            "top_p": 0.9,
            "stop": ["STOP"],
        },
        {"outputs": [{"text": "Hi!", "stop_reason": "stop"}]},
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE: 0.5,
            SPANDATA.GEN_AI_REQUEST_TOP_P: 0.9,
            SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES: ["STOP"],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["stop"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {
                    "role": "user",
                    "parts": [{"type": "text", "content": "<s>[INST] Hello! [/INST]"}],
                }
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="mistral",
    ),
]

# attributes that hold JSON-serialized prompts and responses.
GEN_AI_MESSAGE_ATTRIBUTES = (
    SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
    SPANDATA.GEN_AI_INPUT_MESSAGES,
    SPANDATA.GEN_AI_TOOL_DEFINITIONS,
    SPANDATA.GEN_AI_OUTPUT_MESSAGES,
)


def _invoke_model_response(client, body, input_tokens=7, output_tokens=3):
    """
    Respond to `InvokeModel` with `body`, and its token usage in the headers.
    """
    return MockResponse(
        client,
        200,
        {
            "content-type": "application/json",
            "content-length": str(len(body)),
            "x-amzn-requestid": "request-id",
            "x-amzn-bedrock-input-token-count": str(input_tokens),
            "x-amzn-bedrock-output-token-count": str(output_tokens),
        },
        body,
    )


def _get_attributes(span):
    """
    Return the span's attributes, with the gen_ai message attributes deserialized.
    """
    return {
        key: json.loads(value) if key in GEN_AI_MESSAGE_ATTRIBUTES else value
        for key, value in span["attributes"].items()
    }


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "model_id, request_body, response_body, expected_attributes", INVOKE_MODEL_CASES
)
def test_invoke_model(
    init_bedrock,
    capture_items,
    trace_mode,
    model_id,
    request_body,
    response_body,
    expected_attributes,
):
    client = init_bedrock(trace_mode, send_default_pii=True)
    response_bytes = json.dumps(response_body).encode()
    bodies = []

    def call():
        response = client.invoke_model(modelId=model_id, body=json.dumps(request_body))
        bodies.append(response["body"].read())

    with _invoke_model_response(client, response_bytes):
        spans = _capture_spans(capture_items, trace_mode, call)

    assert bodies == [response_bytes]

    span = _get_bedrock_span(spans)
    attributes = _get_attributes(span)
    assert span["name"] == f"generate_content {model_id}"
    assert span["status"] == "ok"
    assert attributes[SPANDATA.SENTRY_OP] == OP.GEN_AI_GENERATE_CONTENT
    assert attributes[SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
    assert attributes[SPANDATA.GEN_AI_OPERATION_NAME] == "generate_content"
    assert attributes[SPANDATA.GEN_AI_REQUEST_MODEL] == model_id
    assert attributes[SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
    assert attributes[SPANDATA.RPC_METHOD] == "InvokeModel"
    assert attributes[SPANDATA.AWS_REQUEST_ID] == "request-id"
    gen_ai_attributes = {
        key: value
        for key, value in attributes.items()
        if key.startswith("gen_ai.")
        and key
        not in (
            SPANDATA.GEN_AI_PROVIDER_NAME,
            SPANDATA.GEN_AI_OPERATION_NAME,
            SPANDATA.GEN_AI_REQUEST_MODEL,
            SPANDATA.GEN_AI_RESPONSE_STREAMING,
        )
    }
    assert gen_ai_attributes == _expected_gen_ai_attributes(
        trace_mode, expected_attributes
    )
    # media content isn't recorded.
    assert MEDIA_BASE64 not in json.dumps(span["attributes"])
    # only the gen_ai span is recorded, not an `http.client.stream` span.
    assert not any(
        span["attributes"].get(SPANDATA.SENTRY_OP) == OP.HTTP_CLIENT_STREAM
        for span in spans
    )


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "init_kwargs, include_prompts, record_inputs, record_outputs",
    GEN_AI_DATA_COLLECTION_CASES,
)
def test_invoke_model_data_collection(
    init_bedrock,
    capture_items,
    trace_mode,
    init_kwargs,
    include_prompts,
    record_inputs,
    record_outputs,
):
    client = init_bedrock(trace_mode, include_prompts=include_prompts, **init_kwargs)
    model_id, request_body, response_body, expected_attributes = INVOKE_MODEL_CASES[
        0
    ].values

    def call():
        response = client.invoke_model(modelId=model_id, body=json.dumps(request_body))
        response["body"].read()

    with _invoke_model_response(client, json.dumps(response_body).encode()):
        spans = _capture_spans(capture_items, trace_mode, call)

    attributes = _get_attributes(_get_bedrock_span(spans))
    expected_attributes = _expected_gen_ai_attributes(trace_mode, expected_attributes)
    for attribute, recorded in (
        (SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS, record_inputs),
        (SPANDATA.GEN_AI_INPUT_MESSAGES, record_inputs),
        (SPANDATA.GEN_AI_TOOL_DEFINITIONS, record_inputs),
        (SPANDATA.GEN_AI_OUTPUT_MESSAGES, record_outputs),
    ):
        if recorded:
            assert attributes[attribute] == expected_attributes[attribute]
        else:
            assert attribute not in attributes
    # the other attributes are always recorded.
    assert attributes[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["end_turn"]
    assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 160


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "read_body, parsed",
    [
        pytest.param(lambda body: body.read(), True, id="read"),
        pytest.param(
            lambda body: b"".join(iter(lambda: body.read(4), b"")),
            True,
            id="read_amt",
        ),
        pytest.param(
            lambda body: b"".join(body.iter_chunks(chunk_size=4)),
            True,
            id="iter_chunks",
        ),
        pytest.param(
            lambda body: (body.read(4), body.close())[0], False, id="close_early"
        ),
    ],
)
def test_invoke_model_body_consumption(
    init_bedrock, capture_items, trace_mode, read_body, parsed
):
    client = init_bedrock(trace_mode, send_default_pii=True)
    response_bytes = json.dumps(
        {"outputs": [{"text": "Hi!", "stop_reason": "stop"}]}
    ).encode()
    bodies = []

    def call():
        response = client.invoke_model(
            modelId="mistral.mistral-7b-instruct-v0:2",
            body=json.dumps({"prompt": "Hello!"}),
        )
        bodies.append(read_body(response["body"]))

    with _invoke_model_response(client, response_bytes):
        spans = _capture_spans(capture_items, trace_mode, call)

    assert bodies == [response_bytes if parsed else response_bytes[:4]]

    span = _get_bedrock_span(spans)
    attributes = span["attributes"]
    assert span["status"] == "ok"
    assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 7
    assert attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3
    if parsed:
        assert attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["stop"]
        assert SPANDATA.GEN_AI_OUTPUT_MESSAGES in attributes
    else:
        assert SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS not in attributes
        assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in attributes


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_invoke_model_body_read_error_is_unchanged(
    init_bedrock, capture_items, trace_mode
):
    client = init_bedrock(trace_mode)
    original_exception = OSError("stream read failed")

    class _FailingBody(Body):
        def read(self, *args, **kwargs):
            raise original_exception

    def respond(request, **kwargs):
        return AWSResponse(
            request.url, 200, {"content-length": "10"}, _FailingBody(b"")
        )

    client.meta.events.register("before-send", respond)

    def call():
        response = client.invoke_model(
            modelId=MODEL_ID, body=json.dumps({"max_tokens": 100})
        )
        with pytest.raises(OSError) as exc_info:
            response["body"].read()
        assert exc_info.value is original_exception

    spans = _capture_spans(capture_items, trace_mode, call)

    span = _get_bedrock_span(spans)
    assert span["status"] == "error"
    assert span["attributes"][SPANDATA.ERROR_TYPE] == "OSError"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_invoke_model_unknown_model(init_bedrock, capture_items, trace_mode):
    client = init_bedrock(trace_mode, send_default_pii=True)
    model_id = "ai21.jamba-1-5-mini-v1:0"

    def call():
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(
                {"messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 1}
            ),
        )
        response["body"].read()

    with _invoke_model_response(client, b'{"choices": []}'):
        spans = _capture_spans(capture_items, trace_mode, call)

    span = _get_bedrock_span(spans)
    assert span["name"] == f"generate_content {model_id}"
    assert {
        key: value
        for key, value in span["attributes"].items()
        if key.startswith("gen_ai.")
    } == {
        SPANDATA.GEN_AI_PROVIDER_NAME: "aws.bedrock",
        SPANDATA.GEN_AI_OPERATION_NAME: "generate_content",
        SPANDATA.GEN_AI_REQUEST_MODEL: model_id,
        SPANDATA.GEN_AI_RESPONSE_STREAMING: False,
        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
        SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
    }


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "make_request_body",
    [
        pytest.param(
            lambda: BytesIO(
                json.dumps({"prompt": "Hello!", "max_gen_len": 1}).encode()
            ),
            id="file_like",
        ),
        pytest.param(
            lambda: json.dumps({"prompt": "x" * 1_000_001, "max_gen_len": 1}).encode(),
            id="large",
        ),
        pytest.param(lambda: b'{"prompt": "Hello!", "max_gen_len": 1', id="malformed"),
    ],
)
def test_invoke_model_bodies_that_are_not_parsed(
    init_bedrock, capture_items, trace_mode, make_request_body
):
    client = init_bedrock(trace_mode, send_default_pii=True)
    # a file-like request body isn't read, and large or malformed request bodies and
    # a large response body aren't parsed. The response is unchanged.
    response_bytes = json.dumps(
        {"generation": "x" * 1_000_001, "stop_reason": "length"}
    ).encode()
    bodies = []

    def call():
        response = client.invoke_model(
            modelId="meta.llama3-8b-instruct-v1:0", body=make_request_body()
        )
        bodies.append(response["body"].read())

    with _invoke_model_response(client, response_bytes):
        spans = _capture_spans(capture_items, trace_mode, call)

    assert bodies == [response_bytes]

    attributes = _get_bedrock_span(spans)["attributes"]
    assert SPANDATA.GEN_AI_REQUEST_MAX_TOKENS not in attributes
    assert SPANDATA.GEN_AI_INPUT_MESSAGES not in attributes
    assert SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS not in attributes
    assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in attributes
    assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 7


def _encode_event_stream_message(headers, payload):
    """
    Encode a message in the `application/vnd.amazon.eventstream` format, with
    string headers.
    https://docs.aws.amazon.com/transcribe/latest/dg/streaming-setting-up.html#streaming-event-stream
    """
    encoded_headers = b"".join(
        struct.pack("B", len(name))
        + name.encode()
        + b"\x07"
        + struct.pack(">H", len(value))
        + value.encode()
        for name, value in headers.items()
    )
    prelude = struct.pack(
        ">II", 16 + len(encoded_headers) + len(payload), len(encoded_headers)
    )
    message = (
        prelude + struct.pack(">I", binascii.crc32(prelude)) + encoded_headers + payload
    )
    return message + struct.pack(">I", binascii.crc32(message))


def _event_stream_response(client, events):
    """
    Respond with an event stream of `(event type, payload)` events. An event type of
    `None` is a `modelStreamErrorException`.
    """
    body = b"".join(
        _encode_event_stream_message(
            {
                ":message-type": "event",
                ":event-type": event_type,
                ":content-type": "application/json",
            }
            if event_type is not None
            else {
                ":message-type": "exception",
                ":exception-type": "modelStreamErrorException",
                ":content-type": "application/json",
            },
            json.dumps(payload).encode(),
        )
        for event_type, payload in events
    )
    return MockResponse(
        client,
        200,
        {
            "content-type": "application/vnd.amazon.eventstream",
            "x-amzn-requestid": "request-id",
        },
        body,
    )


def _invoke_model_stream_chunks(*chunks):
    return [
        ("chunk", {"bytes": base64.b64encode(json.dumps(chunk).encode()).decode()})
        for chunk in chunks
    ]


CONVERSE_STREAM_EVENTS = [
    ("messageStart", {"role": "assistant"}),
    ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Let me "}}),
    ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "check."}}),
    ("contentBlockStop", {"contentBlockIndex": 0}),
    (
        "contentBlockStart",
        {
            "contentBlockIndex": 1,
            "start": {"toolUse": {"toolUseId": "tool-1", "name": "get_weather"}},
        },
    ),
    (
        "contentBlockDelta",
        {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"city": '}}},
    ),
    (
        "contentBlockDelta",
        {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '"Paris"}'}}},
    ),
    ("contentBlockStop", {"contentBlockIndex": 1}),
    ("messageStop", {"stopReason": "tool_use"}),
    (
        "metadata",
        {
            "usage": {"inputTokens": 12, "outputTokens": 8, "totalTokens": 20},
            "metrics": {"latencyMs": 100},
        },
    ),
]

CONVERSE_STREAM_PARAMS = {
    "modelId": MODEL_ID,
    "messages": [{"role": "user", "content": [{"text": "Weather in Paris?"}]}],
    "inferenceConfig": {"maxTokens": 100},
}


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "init_kwargs, include_prompts, record_inputs, record_outputs",
    GEN_AI_DATA_COLLECTION_CASES,
)
def test_converse_stream(
    init_bedrock,
    capture_items,
    trace_mode,
    init_kwargs,
    include_prompts,
    record_inputs,
    record_outputs,
):
    client = init_bedrock(trace_mode, include_prompts=include_prompts, **init_kwargs)
    received_events = []

    def call():
        response = client.converse_stream(**CONVERSE_STREAM_PARAMS)
        received_events.extend(response["stream"])

    with _event_stream_response(client, CONVERSE_STREAM_EVENTS):
        spans = _capture_spans(capture_items, trace_mode, call)

    # the caller receives the events unchanged.
    assert received_events == [
        {event_type: payload} for event_type, payload in CONVERSE_STREAM_EVENTS
    ]

    span = _get_bedrock_span(spans)
    attributes = _get_attributes(span)
    assert span["name"] == f"chat {MODEL_ID}"
    assert span["status"] == "ok"
    assert attributes[SPANDATA.SENTRY_OP] == OP.GEN_AI_CHAT
    assert attributes[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert attributes[SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
    assert attributes[SPANDATA.GEN_AI_REQUEST_MODEL] == MODEL_ID
    assert attributes[SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert attributes[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100
    assert attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["tool_use"]
    assert attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 12
    assert attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 8
    assert attributes[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert attributes[SPANDATA.RPC_METHOD] == "ConverseStream"
    assert attributes[SPANDATA.AWS_REQUEST_ID] == "request-id"

    if record_inputs:
        assert attributes[SPANDATA.GEN_AI_INPUT_MESSAGES] == [
            {
                "role": "user",
                "parts": [{"type": "text", "content": "Weather in Paris?"}],
            }
        ]
    else:
        assert SPANDATA.GEN_AI_INPUT_MESSAGES not in attributes

    if record_outputs:
        assert attributes[SPANDATA.GEN_AI_OUTPUT_MESSAGES] == [
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "Let me check."},
                    {
                        "type": "tool_call",
                        "id": "tool-1",
                        "name": "get_weather",
                        "arguments": {"city": "Paris"},
                    },
                ],
            }
        ]
    else:
        assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in attributes


def _consume_until_error(stream):
    events = []
    with pytest.raises(EventStreamError) as exc_info:
        for event in stream:
            events.append(event)
    assert exc_info.value.response["Error"]["Code"] == "modelStreamErrorException"
    return events


def _break_after_first_event(stream):
    events = []
    for event in stream:
        events.append(event)
        break
    return events


def _close_unread(stream):
    stream.close()
    return []


def _close_after_first_event(stream):
    events = [next(iter(stream))]
    stream.close()
    return events


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "events, consume, received, status",
    [
        pytest.param(CONVERSE_STREAM_EVENTS, list, 10, "ok", id="exhausted"),
        pytest.param(
            CONVERSE_STREAM_EVENTS, _break_after_first_event, 1, "ok", id="break"
        ),
        pytest.param(
            CONVERSE_STREAM_EVENTS, _close_after_first_event, 1, "ok", id="close"
        ),
        pytest.param(
            CONVERSE_STREAM_EVENTS,
            _close_unread,
            0,
            "ok",
            id="close_unread",
        ),
        pytest.param(
            CONVERSE_STREAM_EVENTS[:2] + [(None, {"message": "Model error"})],
            _consume_until_error,
            2,
            "error",
            id="error",
        ),
    ],
)
def test_converse_stream_consumption(
    init_bedrock, capture_items, trace_mode, events, consume, received, status
):
    client = init_bedrock(trace_mode, send_default_pii=True)
    received_events = []

    def call():
        response = client.converse_stream(**CONVERSE_STREAM_PARAMS)
        received_events.extend(consume(response["stream"]))

    with _event_stream_response(client, events):
        spans = _capture_spans(capture_items, trace_mode, call)

    assert received_events == [
        {event_type: payload} for event_type, payload in events[:received]
    ]

    # the span finishes once, with the response received so far.
    span = _get_bedrock_span(spans)
    attributes = _get_attributes(span)
    assert span["status"] == status
    if status == "error":
        assert attributes[SPANDATA.ERROR_TYPE] == "modelStreamErrorException"
    else:
        assert SPANDATA.ERROR_TYPE not in attributes

    if received == len(CONVERSE_STREAM_EVENTS):
        assert attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["tool_use"]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS not in attributes
        assert SPANDATA.GEN_AI_USAGE_INPUT_TOKENS not in attributes

    if received >= 2:
        assert attributes[SPANDATA.GEN_AI_OUTPUT_MESSAGES][0]["parts"][0] == {
            "type": "text",
            "content": "Let me check." if received > 2 else "Let me ",
        }
    else:
        assert SPANDATA.GEN_AI_OUTPUT_MESSAGES not in attributes


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_converse_stream_without_parent_span(init_bedrock, capture_items, trace_mode):
    client = init_bedrock(trace_mode, send_default_pii=True)
    items = capture_items("transaction", "span")

    with _event_stream_response(client, CONVERSE_STREAM_EVENTS):
        response = client.converse_stream(**CONVERSE_STREAM_PARAMS)
        received_events = list(response["stream"])
    sentry_sdk.flush()

    assert received_events == [
        {event_type: payload} for event_type, payload in CONVERSE_STREAM_EVENTS
    ]
    assert not items


INVOKE_MODEL_STREAM_CASES = [
    pytest.param(
        "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Weather in Paris?"}],
        },
        [
            {
                "type": "message_start",
                "message": {
                    "role": "assistant",
                    "content": [],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "I need the tool."},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "abc"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Let me "},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "check."},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "get_weather",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"city": '},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'},
            },
            {"type": "content_block_stop", "index": 2},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 25},
            },
            {
                "type": "message_stop",
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 10,
                    "outputTokenCount": 25,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["tool_use"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 110,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 25,
            SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 100,
            SPANDATA.GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS: 0,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {
                    "role": "user",
                    "parts": [{"type": "text", "content": "Weather in Paris?"}],
                }
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "reasoning", "content": "I need the tool."},
                        {"type": "text", "content": "Let me check."},
                        {
                            "type": "tool_call",
                            "id": "tool-1",
                            "name": "get_weather",
                            "arguments": {"city": "Paris"},
                        },
                    ],
                }
            ],
        },
        id="anthropic_claude",
    ),
    pytest.param(
        "amazon.nova-lite-v1:0",
        {
            "messages": [{"role": "user", "content": [{"text": "Hello!"}]}],
            "inferenceConfig": {"max_new_tokens": 100},
        },
        [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "Hi"}, "contentBlockIndex": 0}},
            {"contentBlockDelta": {"delta": {"text": "!"}, "contentBlockIndex": 0}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {
                "metadata": {"usage": {"inputTokens": 10, "outputTokens": 5}},
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 10,
                    "outputTokenCount": 5,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["end_turn"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="amazon_nova",
    ),
    pytest.param(
        "amazon.titan-text-express-v1",
        {"inputText": "Hello!", "textGenerationConfig": {"maxTokenCount": 100}},
        [
            {
                "outputText": "Hi",
                "index": 0,
                "totalOutputTextTokenCount": 1,
                "completionReason": None,
                "inputTextTokenCount": 10,
            },
            {
                "outputText": "!",
                "index": 0,
                "totalOutputTextTokenCount": 5,
                "completionReason": "FINISH",
                "inputTextTokenCount": None,
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 10,
                    "outputTokenCount": 5,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["FINISH"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="amazon_titan",
    ),
    pytest.param(
        "meta.llama3-8b-instruct-v1:0",
        {"prompt": "Hello!", "max_gen_len": 100},
        [
            {
                "generation": "Hi",
                "prompt_token_count": 10,
                "generation_token_count": 1,
                "stop_reason": None,
            },
            {
                "generation": "!",
                "prompt_token_count": None,
                "generation_token_count": 5,
                "stop_reason": "stop",
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 10,
                    "outputTokenCount": 5,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["stop"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 10,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 5,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="meta_llama",
    ),
    pytest.param(
        "cohere.command-r-v1:0",
        {"message": "Hello!", "max_tokens": 100},
        [
            {"is_finished": False, "event_type": "stream-start", "generation_id": "1"},
            {"is_finished": False, "event_type": "text-generation", "text": "Hi"},
            {"is_finished": False, "event_type": "text-generation", "text": "!"},
            {
                "is_finished": True,
                "event_type": "stream-end",
                "finish_reason": "COMPLETE",
                "response": {"text": "Hi!"},
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 7,
                    "outputTokenCount": 3,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["COMPLETE"],
            # the chunks have no usage; it's in Bedrock's invocation metrics.
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="cohere_command_r",
    ),
    pytest.param(
        "cohere.command-text-v14",
        {"prompt": "Hello!", "max_tokens": 100},
        [
            {"generations": [{"text": "Hi"}]},
            {
                "generations": [{"text": "!", "finish_reason": "COMPLETE"}],
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 7,
                    "outputTokenCount": 3,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["COMPLETE"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="cohere_command",
    ),
    pytest.param(
        "mistral.mistral-7b-instruct-v0:2",
        {"prompt": "Hello!", "max_tokens": 100},
        [
            {"outputs": [{"text": "Hi", "stop_reason": None}]},
            {
                "outputs": [{"text": "!", "stop_reason": "stop"}],
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 7,
                    "outputTokenCount": 3,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS: 100,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: ["stop"],
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
            SPANDATA.GEN_AI_INPUT_MESSAGES: [
                {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
            ],
            SPANDATA.GEN_AI_OUTPUT_MESSAGES: [
                {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
            ],
        },
        id="mistral",
    ),
    pytest.param(
        "ai21.jamba-1-5-mini-v1:0",
        {"messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100},
        [
            {"choices": [{"delta": {"content": "Hi!"}}]},
            {
                "choices": [{"finish_reason": "stop"}],
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 7,
                    "outputTokenCount": 3,
                },
            },
        ],
        {
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: 7,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: 3,
        },
        id="unknown_model",
    ),
]


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "model_id, request_body, chunks, expected_attributes", INVOKE_MODEL_STREAM_CASES
)
def test_invoke_model_with_response_stream(
    init_bedrock,
    capture_items,
    trace_mode,
    model_id,
    request_body,
    chunks,
    expected_attributes,
):
    client = init_bedrock(trace_mode, send_default_pii=True)
    received_chunks = []

    def call():
        response = client.invoke_model_with_response_stream(
            modelId=model_id, body=json.dumps(request_body)
        )
        received_chunks.extend(
            json.loads(event["chunk"]["bytes"]) for event in response["body"]
        )

    with _event_stream_response(client, _invoke_model_stream_chunks(*chunks)):
        spans = _capture_spans(capture_items, trace_mode, call)

    assert received_chunks == chunks

    span = _get_bedrock_span(spans)
    attributes = _get_attributes(span)
    assert span["name"] == f"generate_content {model_id}"
    assert span["status"] == "ok"
    assert attributes[SPANDATA.SENTRY_OP] == OP.GEN_AI_GENERATE_CONTENT
    assert attributes[SPANDATA.RPC_METHOD] == "InvokeModelWithResponseStream"
    assert {
        key: value
        for key, value in attributes.items()
        if key.startswith("gen_ai.")
        and key
        not in (
            SPANDATA.GEN_AI_PROVIDER_NAME,
            SPANDATA.GEN_AI_OPERATION_NAME,
            SPANDATA.GEN_AI_REQUEST_MODEL,
        )
    } == {SPANDATA.GEN_AI_RESPONSE_STREAMING: True, **expected_attributes}
    assert attributes[SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
    assert attributes[SPANDATA.GEN_AI_OPERATION_NAME] == "generate_content"
    assert attributes[SPANDATA.GEN_AI_REQUEST_MODEL] == model_id


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "init_kwargs, include_prompts, record_inputs, record_outputs",
    GEN_AI_DATA_COLLECTION_CASES,
)
def test_invoke_model_with_response_stream_data_collection(
    init_bedrock,
    capture_items,
    trace_mode,
    init_kwargs,
    include_prompts,
    record_inputs,
    record_outputs,
):
    client = init_bedrock(trace_mode, include_prompts=include_prompts, **init_kwargs)
    model_id, request_body, chunks, expected_attributes = INVOKE_MODEL_STREAM_CASES[
        0
    ].values

    def call():
        response = client.invoke_model_with_response_stream(
            modelId=model_id, body=json.dumps(request_body)
        )
        for _ in response["body"]:
            pass

    with _event_stream_response(client, _invoke_model_stream_chunks(*chunks)):
        spans = _capture_spans(capture_items, trace_mode, call)

    attributes = _get_attributes(_get_bedrock_span(spans))
    for attribute, recorded in (
        (SPANDATA.GEN_AI_INPUT_MESSAGES, record_inputs),
        (SPANDATA.GEN_AI_OUTPUT_MESSAGES, record_outputs),
    ):
        if recorded:
            assert attributes[attribute] == expected_attributes[attribute]
        else:
            assert attribute not in attributes
    assert (
        attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS]
        == expected_attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS]
    )


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_invoke_model_with_response_stream_skips_malformed_chunks(
    init_bedrock, capture_items, trace_mode
):
    client = init_bedrock(trace_mode, send_default_pii=True)
    malformed_chunk = ("chunk", {"bytes": base64.b64encode(b"not json").decode()})
    events = [malformed_chunk] + _invoke_model_stream_chunks(
        {
            "outputs": [{"text": "Hi!", "stop_reason": "stop"}],
            "amazon-bedrock-invocationMetrics": {
                "inputTokenCount": 7,
                "outputTokenCount": 3,
            },
        }
    )
    received_chunks = []

    def call():
        response = client.invoke_model_with_response_stream(
            modelId="mistral.mistral-7b-instruct-v0:2",
            body=json.dumps({"prompt": "Hello!"}),
        )
        received_chunks.extend(event["chunk"]["bytes"] for event in response["body"])

    with _event_stream_response(client, events):
        spans = _capture_spans(capture_items, trace_mode, call)

    assert received_chunks[0] == b"not json"
    assert len(received_chunks) == 2

    attributes = _get_attributes(_get_bedrock_span(spans))
    assert attributes[SPANDATA.GEN_AI_OUTPUT_MESSAGES] == [
        {"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}
    ]
    assert attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
def test_invoke_model_with_response_stream_error_is_unchanged(
    init_bedrock, capture_items, trace_mode
):
    client = init_bedrock(trace_mode)
    events = _invoke_model_stream_chunks(
        {"outputs": [{"text": "Hi", "stop_reason": None}]}
    ) + [(None, {"message": "Model error"})]
    received_chunks = []

    def call():
        response = client.invoke_model_with_response_stream(
            modelId="mistral.mistral-7b-instruct-v0:2",
            body=json.dumps({"prompt": "Hello!"}),
        )
        received_chunks.extend(_consume_until_error(response["body"]))

    with _event_stream_response(client, events):
        spans = _capture_spans(capture_items, trace_mode, call)

    assert len(received_chunks) == 1

    span = _get_bedrock_span(spans)
    assert span["status"] == "error"
    assert span["attributes"][SPANDATA.ERROR_TYPE] == "modelStreamErrorException"
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

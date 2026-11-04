import json

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3.consts import AI_ORIGIN, ORIGIN

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


@pytest.fixture
def init_bedrock(sentry_init):
    def inner(trace_mode, **kwargs):
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
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


def _get_bedrock_span(spans, origin=AI_ORIGIN):
    (span,) = (
        span
        for span in spans
        if span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == origin
    )
    return span


@pytest.mark.parametrize("trace_mode", TRACE_MODES)
@pytest.mark.parametrize(
    "init_kwargs, record_inputs, record_outputs",
    [
        pytest.param({}, False, False, id="default"),
        pytest.param({"send_default_pii": True}, True, True, id="send_default_pii"),
        pytest.param(
            {"data_collection": {"gen_ai": {"inputs": True, "outputs": False}}},
            True,
            False,
            id="data_collection_inputs_only",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "data_collection": {"gen_ai": {"inputs": False, "outputs": True}},
            },
            False,
            True,
            id="data_collection_wins_over_send_default_pii",
        ),
    ],
)
def test_converse(
    init_bedrock,
    capture_items,
    trace_mode,
    init_kwargs,
    record_inputs,
    record_outputs,
):
    client = init_bedrock(trace_mode, **init_kwargs)
    params = {
        "modelId": MODEL_ID,
        "system": [{"text": "You are a helpful assistant."}],
        "messages": [{"role": "user", "content": [{"text": "Hello!"}]}],
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

    with Stubber(client) as stubber:
        stubber.add_response("converse", CONVERSE_RESPONSE, params)
        responses = []
        spans = _capture_spans(
            capture_items,
            trace_mode,
            lambda: responses.append(client.converse(**params)),
        )

    assert responses[0]["output"] == CONVERSE_RESPONSE["output"]

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
        assert json.loads(attributes[SPANDATA.GEN_AI_INPUT_MESSAGES]) == [
            {"role": "user", "parts": [{"type": "text", "content": "Hello!"}]}
        ]
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
    assert json.loads(attributes[SPANDATA.GEN_AI_INPUT_MESSAGES]) == [
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

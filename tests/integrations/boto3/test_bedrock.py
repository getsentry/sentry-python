import io
import json
from unittest.mock import Mock

import boto3
import botocore
import botocore.session
import pytest
import urllib3
from botocore.eventstream import EventStream, EventStreamMessage
from botocore.exceptions import ClientError
from botocore.loaders import Loader
from botocore.parsers import EventStreamJSONParser
from botocore.stub import Stubber
from packaging.version import Version

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration

BOTOCORE_VERSION = Version(botocore.__version__)

pytestmark = pytest.mark.skipif(
    "bedrock-runtime" not in Loader().list_available_services("service-2"),
    reason="botocore is too old for bedrock-runtime.",
)


@pytest.fixture
def converse_response():
    usage = {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30}
    if BOTOCORE_VERSION >= Version("1.37.24"):
        usage["cacheReadInputTokens"] = 80
        usage["cacheWriteInputTokens"] = 20

    return {
        "output": {"message": {"role": "assistant", "content": [{"text": "Hello"}]}},
        "stopReason": "end_turn",
        "usage": usage,
        "metrics": {"latencyMs": 500},
    }


@pytest.fixture
def converse_stream_response():
    usage = {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30}
    if BOTOCORE_VERSION >= Version("1.37.24"):
        usage["cacheReadInputTokens"] = 80
        usage["cacheWriteInputTokens"] = 20

    stream_shape = (
        botocore.session.get_session()
        .get_service_model("bedrock-runtime")
        .operation_model("ConverseStream")
        .output_shape.members["stream"]
    )
    raw_stream = urllib3.HTTPResponse(body=io.BytesIO(b""), preload_content=False)
    stream = EventStream(
        raw_stream, stream_shape, EventStreamJSONParser(), "ConverseStream"
    )
    stream._event_generator = iter(
        [
            EventStreamMessage(
                prelude=None,
                headers={":message-type": "event", ":event-type": "messageStart"},
                payload=json.dumps({"role": "assistant"}).encode(),
                crc=None,
            ),
            EventStreamMessage(
                prelude=None,
                headers={":message-type": "event", ":event-type": "contentBlockDelta"},
                payload=json.dumps(
                    {"contentBlockIndex": 0, "delta": {"text": "Hello"}}
                ).encode(),
                crc=None,
            ),
            EventStreamMessage(
                prelude=None,
                headers={":message-type": "event", ":event-type": "messageStop"},
                payload=json.dumps({"stopReason": "end_turn"}).encode(),
                crc=None,
            ),
            EventStreamMessage(
                prelude=None,
                headers={":message-type": "event", ":event-type": "metadata"},
                payload=json.dumps(
                    {"usage": usage, "metrics": {"latencyMs": 500}}
                ).encode(),
                crc=None,
            ),
        ]
    )

    return {
        "stream": stream,
        "ResponseMetadata": {
            "RequestId": "test-id",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {},
            "RetryAttempts": 0,
        },
    }


@pytest.fixture
def converse_stream_error_response():
    stream_shape = (
        botocore.session.get_session()
        .get_service_model("bedrock-runtime")
        .operation_model("ConverseStream")
        .output_shape.members["stream"]
    )
    raw_stream = urllib3.HTTPResponse(body=io.BytesIO(b""), preload_content=False)
    stream = EventStream(
        raw_stream, stream_shape, EventStreamJSONParser(), "ConverseStream"
    )
    stream._event_generator = iter(
        [
            EventStreamMessage(
                prelude=None,
                headers={":message-type": "event", ":event-type": "messageStart"},
                payload=json.dumps({"role": "assistant"}).encode(),
                crc=None,
            ),
            EventStreamMessage(
                prelude=None,
                headers={":message-type": "event", ":event-type": "contentBlockDelta"},
                payload=json.dumps(
                    {"contentBlockIndex": 0, "delta": {"text": "Hi"}}
                ).encode(),
                crc=None,
            ),
            EventStreamMessage(
                prelude=None,
                headers={
                    ":message-type": "exception",
                    ":exception-type": "modelStreamErrorException",
                },
                payload=json.dumps({"message": "Stream failed"}).encode(),
                crc=None,
            ),
        ]
    )

    return {
        "stream": stream,
        "ResponseMetadata": {
            "RequestId": "test-id",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {},
            "RetryAttempts": 0,
        },
    }


@pytest.fixture
def content_block_messages():
    messages = [
        {"role": "user", "content": [{"text": "What is the weather in Paris?"}]},
        {
            "role": "user",
            "content": [{"image": {"format": "png", "source": {"bytes": b"image"}}}],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "tooluse_1",
                        "name": "get_weather",
                        "input": {"location": "Paris"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "tooluse_1",
                        "content": [
                            {"json": {"temperature": 18, "unit": "celsius"}},
                            {"text": "Partly cloudy"},
                        ],
                        "status": "success",
                    }
                }
            ],
        },
    ]

    expected_input_messages = [
        {
            "role": "user",
            "parts": [{"type": "text", "content": "What is the weather in Paris?"}],
        },
        {"role": "user", "parts": [{"type": "blob", "modality": "image"}]},
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "name": "get_weather",
                    "arguments": {"location": "Paris"},
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "type": "tool_call_response",
                    "response": [
                        {"json": {"temperature": 18, "unit": "celsius"}},
                        {"text": "Partly cloudy"},
                    ],
                }
            ],
        },
    ]

    if BOTOCORE_VERSION >= Version("1.34.131"):
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "document": {
                            "format": "pdf",
                            "name": "report",
                            "source": {"bytes": b"document"},
                        }
                    }
                ],
            }
        )
        expected_input_messages.append(
            {"role": "user", "parts": [{"type": "blob", "modality": "document"}]}
        )

    if BOTOCORE_VERSION >= Version("1.35.74"):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": [
                        {"video": {"format": "mp4", "source": {"bytes": b"video"}}}
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "video": {
                                "format": "mp4",
                                "source": {
                                    "s3Location": {
                                        "uri": "s3://my-bucket/videos/clip.mp4"
                                    }
                                },
                            }
                        }
                    ],
                },
            ]
        )
        expected_input_messages.extend(
            [
                {"role": "user", "parts": [{"type": "blob", "modality": "video"}]},
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "uri",
                            "modality": "video",
                            "uri": "s3://my-bucket/videos/clip.mp4",
                        }
                    ],
                },
            ]
        )

    if BOTOCORE_VERSION >= Version("1.37.0"):
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "reasoningContent": {
                            "reasoningText": {
                                "text": "The user wants the weather in Paris.",
                                "signature": "signature",
                            }
                        }
                    }
                ],
            }
        )
        expected_input_messages.append(
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "reasoning",
                        "content": "The user wants the weather in Paris.",
                    }
                ],
            }
        )

    if BOTOCORE_VERSION >= Version("1.38.3"):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "image": {
                                "format": "png",
                                "source": {
                                    "s3Location": {
                                        "uri": "s3://my-bucket/images/photo.png"
                                    }
                                },
                            }
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "document": {
                                "format": "pdf",
                                "name": "manual",
                                "source": {
                                    "s3Location": {
                                        "uri": "s3://my-bucket/documents/manual.pdf"
                                    }
                                },
                            }
                        }
                    ],
                },
            ]
        )
        expected_input_messages.extend(
            [
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "uri",
                            "modality": "image",
                            "uri": "s3://my-bucket/images/photo.png",
                        }
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "uri",
                            "modality": "document",
                            "uri": "s3://my-bucket/documents/manual.pdf",
                        }
                    ],
                },
            ]
        )

    if BOTOCORE_VERSION >= Version("1.42.1"):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": [
                        {"audio": {"format": "mp3", "source": {"bytes": b"audio"}}}
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "audio": {
                                "format": "mp3",
                                "source": {
                                    "s3Location": {
                                        "uri": "s3://my-bucket/audio/question.mp3"
                                    }
                                },
                            }
                        }
                    ],
                },
            ]
        )
        expected_input_messages.extend(
            [
                {"role": "user", "parts": [{"type": "blob", "modality": "audio"}]},
                {
                    "role": "user",
                    "parts": [
                        {
                            "type": "uri",
                            "modality": "audio",
                            "uri": "s3://my-bucket/audio/question.mp3",
                        }
                    ],
                },
            ]
        )

    return messages, expected_input_messages


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse(
    sentry_init,
    capture_items,
    capture_events,
    span_streaming,
    stream_gen_ai_spans,
    converse_response,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    stubber = Stubber(client)
    stubber.add_response("converse", converse_response)

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                inferenceConfig={
                    "maxTokens": 1024,
                    "temperature": 0.7,
                    "topP": 0.9,
                },
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
        if BOTOCORE_VERSION < Version("1.37.24"):
            assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
            return
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 110
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20
    else:
        events = capture_events()

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                inferenceConfig={
                    "maxTokens": 1024,
                    "temperature": 0.7,
                    "topP": 0.9,
                },
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
        if BOTOCORE_VERSION < Version("1.37.24"):
            assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
            return
        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 110
        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_error(
    sentry_init, capture_items, capture_events, span_streaming, stream_gen_ai_spans
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    original_exception = ClientError(
        error_response={
            "Error": {"Code": "AccessDeniedException", "Message": "Access denied"},
            "ResponseMetadata": {
                "RequestId": "test-id",
                "HostId": "",
                "HTTPStatusCode": 403,
                "HTTPHeaders": {},
                "RetryAttempts": 1,
            },
        },
        operation_name="Converse",
    )

    def raise_client_error(**kwargs):
        raise original_exception

    client.meta.events.register("before-parameter-build", raise_client_error)

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"), pytest.raises(ClientError):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                inferenceConfig={
                    "maxTokens": 1024,
                    "temperature": 0.7,
                    "topP": 0.9,
                },
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["attributes"][SPANDATA.ERROR_TYPE] == "AccessDeniedException"
        assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 403
        assert span["attributes"][SPANDATA.AWS_REQUEST_ID] == "test-id"
        assert span["attributes"][SPANDATA.HTTP_REQUEST_RESEND_COUNT] == 1
        assert span["status"] == "error"
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="parent"), pytest.raises(ClientError):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                inferenceConfig={
                    "maxTokens": 1024,
                    "temperature": 0.7,
                    "topP": 0.9,
                },
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.ERROR_TYPE] == "AccessDeniedException"
        assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 403
        assert span["data"][SPANDATA.AWS_REQUEST_ID] == "test-id"
        assert span["data"][SPANDATA.HTTP_REQUEST_RESEND_COUNT] == 1
        assert span["status"] == "internal_error"


@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_tool_definitions(
    sentry_init,
    capture_items,
    capture_events,
    data_collection,
    span_streaming,
    stream_gen_ai_spans,
    converse_response,
):
    if data_collection:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            data_collection={"gen_ai": {"inputs": True}},
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )
    else:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )

    tools: list = [
        {
            "toolSpec": {
                "name": "get_weather",
                "description": "Get the current weather in a given location",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    }
                },
            }
        },
    ]
    expected_tool_definitions = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    ]

    if BOTOCORE_VERSION >= Version("1.40.62"):
        tools.append({"systemTool": {"name": "web_search"}})
        expected_tool_definitions.append({"type": "web_search"})

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    stubber = Stubber(client)
    stubber.add_response("converse", converse_response)

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                toolConfig={"tools": tools},
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_TOOL_DEFINITIONS])
            == expected_tool_definitions
        )
    else:
        events = capture_events()

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                toolConfig={"tools": tools},
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_TOOL_DEFINITIONS])
            == expected_tool_definitions
        )


@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_input_attributes(
    sentry_init,
    capture_items,
    capture_events,
    data_collection,
    span_streaming,
    stream_gen_ai_spans,
    converse_response,
    content_block_messages,
):
    if data_collection:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            data_collection={"gen_ai": {"inputs": True}},
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )
    else:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            send_default_pii=True,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )

    messages, expected_input_messages = content_block_messages

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    stubber = Stubber(client)
    stubber.add_response("converse", converse_response)

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=messages,
                system=[
                    {"text": "You are a helpful assistant."},
                    {"text": "Be concise and clear."},
                ],
            )

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
            {"type": "text", "content": "You are a helpful assistant."},
            {"type": "text", "content": "Be concise and clear."},
        ]
        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )
    else:
        events = capture_events()

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=messages,
                system=[
                    {"text": "You are a helpful assistant."},
                    {"text": "Be concise and clear."},
                ],
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
            {"type": "text", "content": "You are a helpful assistant."},
            {"type": "text", "content": "Be concise and clear."},
        ]
        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_stream(
    sentry_init,
    capture_items,
    capture_events,
    span_streaming,
    stream_gen_ai_spans,
    converse_stream_response,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    mock_http = Mock(status_code=200)
    client.meta.events.register(
        "before-call.bedrock-runtime.ConverseStream",
        lambda **kwargs: (mock_http, converse_stream_response),
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                inferenceConfig={
                    "maxTokens": 1024,
                    "temperature": 0.7,
                    "topP": 0.9,
                },
            )
            for _ in response["stream"]:
                pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
        if BOTOCORE_VERSION < Version("1.37.24"):
            assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
            return
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 110
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                inferenceConfig={
                    "maxTokens": 1024,
                    "temperature": 0.7,
                    "topP": 0.9,
                },
            )
            for _ in response["stream"]:
                pass

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9

        assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
        assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
        if BOTOCORE_VERSION < Version("1.37.24"):
            assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
            return
        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 110
        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_stream_error_during_iteration(
    sentry_init,
    capture_items,
    capture_events,
    span_streaming,
    stream_gen_ai_spans,
    converse_stream_error_response,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    mock_http = Mock(status_code=200)
    client.meta.events.register(
        "before-call.bedrock-runtime.ConverseStream",
        lambda **kwargs: (mock_http, converse_stream_error_response),
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
            )
            with pytest.raises(ClientError):
                for _ in response["stream"]:
                    pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert span["status"] == "error"
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
            )
            with pytest.raises(ClientError):
                for _ in response["stream"]:
                    pass

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["status"] == "internal_error"


@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_stream_tool_definitions(
    sentry_init,
    capture_items,
    capture_events,
    data_collection,
    span_streaming,
    stream_gen_ai_spans,
    converse_stream_response,
):
    if data_collection:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            data_collection={"gen_ai": {"inputs": True}},
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )
    else:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )

    tools: list = [
        {
            "toolSpec": {
                "name": "get_weather",
                "description": "Get the current weather in a given location",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    }
                },
            }
        },
    ]
    expected_tool_definitions = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    ]

    if BOTOCORE_VERSION >= Version("1.40.62"):
        tools.append({"systemTool": {"name": "web_search"}})
        expected_tool_definitions.append({"type": "web_search"})

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    mock_http = Mock(status_code=200)
    client.meta.events.register(
        "before-call.bedrock-runtime.ConverseStream",
        lambda **kwargs: (mock_http, converse_stream_response),
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                toolConfig={"tools": tools},
            )
            for _ in response["stream"]:
                pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_TOOL_DEFINITIONS])
            == expected_tool_definitions
        )
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
                toolConfig={"tools": tools},
            )
            for _ in response["stream"]:
                pass

        (transaction,) = events
        (span,) = transaction["spans"]

        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_TOOL_DEFINITIONS])
            == expected_tool_definitions
        )


@pytest.mark.parametrize("data_collection", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_stream_input_attributes(
    sentry_init,
    capture_items,
    capture_events,
    data_collection,
    span_streaming,
    stream_gen_ai_spans,
    converse_stream_response,
    content_block_messages,
):
    if data_collection:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            data_collection={"gen_ai": {"inputs": True}},
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )
    else:
        sentry_init(
            traces_sample_rate=1.0,
            integrations=[Boto3Integration()],
            send_default_pii=True,
            stream_gen_ai_spans=stream_gen_ai_spans,
            trace_lifecycle="stream" if span_streaming else "static",
        )

    messages, expected_input_messages = content_block_messages

    session = boto3.Session(
        aws_access_key_id="-", aws_secret_access_key="-", region_name="us-east-1"
    )
    client = session.client("bedrock-runtime")
    mock_http = Mock(status_code=200)
    client.meta.events.register(
        "before-call.bedrock-runtime.ConverseStream",
        lambda **kwargs: (mock_http, converse_stream_response),
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=messages,
                system=[
                    {"text": "You are a helpful assistant."},
                    {"text": "Be concise and clear."},
                ],
            )
            for _ in response["stream"]:
                pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.GEN_AI_CHAT
        )

        assert json.loads(span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
            {"type": "text", "content": "You are a helpful assistant."},
            {"type": "text", "content": "Be concise and clear."},
        ]
        assert (
            json.loads(span["attributes"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=messages,
                system=[
                    {"text": "You are a helpful assistant."},
                    {"text": "Be concise and clear."},
                ],
            )
            for _ in response["stream"]:
                pass

        (transaction,) = events
        (span,) = transaction["spans"]

        assert json.loads(span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]) == [
            {"type": "text", "content": "You are a helpful assistant."},
            {"type": "text", "content": "Be concise and clear."},
        ]
        assert (
            json.loads(span["data"][SPANDATA.GEN_AI_INPUT_MESSAGES])
            == expected_input_messages
        )

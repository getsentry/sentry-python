import io
from unittest.mock import Mock

import boto3
import botocore
import botocore.session
import pytest
import urllib3
from botocore.eventstream import EventStream
from botocore.exceptions import ClientError, EventStreamError
from botocore.loaders import Loader
from botocore.parsers import EventStreamJSONParser
from botocore.stub import Stubber
from packaging.version import Version

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration

BOTOCORE_VERSION = Version(botocore.__version__)

BEFORE_CALL_EVENT = "before-call.bedrock-runtime.ConverseStream"


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

    output_shape = (
        botocore.session.get_session()
        .get_service_model("bedrock-runtime")
        .operation_model("ConverseStream")
        .output_shape
    )
    raw_stream = urllib3.HTTPResponse(body=io.BytesIO(b""), preload_content=False)
    stream = EventStream(
        raw_stream, output_shape, EventStreamJSONParser(), "ConverseStream"
    )
    stream._event_generator = iter(
        [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hello"}}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": usage, "metrics": {"latencyMs": 500}}},
        ]
    )
    stream._parse_event = lambda event: event

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
    error = EventStreamError(
        {"Error": {"Code": "throttlingException", "Message": "Rate exceeded"}},
        "ConverseStream",
    )

    def error_events():
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hi"}}}
        raise error

    output_shape = (
        botocore.session.get_session()
        .get_service_model("bedrock-runtime")
        .operation_model("ConverseStream")
        .output_shape
    )
    raw_stream = urllib3.HTTPResponse(body=io.BytesIO(b""), preload_content=False)
    stream = EventStream(
        raw_stream, output_shape, EventStreamJSONParser(), "ConverseStream"
    )
    stream._event_generator = error_events()
    stream._parse_event = lambda event: event

    return {
        "stream": stream,
        "ResponseMetadata": {
            "RequestId": "test-id",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {},
            "RetryAttempts": 0,
        },
    }


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
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
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
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

        assert span["data"][SPANDATA.ERROR_TYPE] == "AccessDeniedException"
        assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 403
        assert span["data"][SPANDATA.AWS_REQUEST_ID] == "test-id"
        assert span["data"][SPANDATA.HTTP_REQUEST_RESEND_COUNT] == 1
        assert span["status"] == "internal_error"


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
        BEFORE_CALL_EVENT, lambda **kwargs: (mock_http, converse_stream_response)
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"):
            response = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
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
            )
            for _ in response["stream"]:
                pass

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
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
        BEFORE_CALL_EVENT,
        lambda **kwargs: (mock_http, converse_stream_error_response),
    )

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"):
            result = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
            )
            with pytest.raises(ClientError):
                for _ in result["stream"]:
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
        assert span["status"] == "error"
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="parent"):
            result = client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
            )
            with pytest.raises(ClientError):
                for _ in result["stream"]:
                    pass

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
        assert span["status"] == "internal_error"


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_stream_error(
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
        operation_name="ConverseStream",
    )

    def raise_client_error(**kwargs):
        raise original_exception

    client.meta.events.register("before-parameter-build", raise_client_error)

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with sentry_sdk.start_transaction(name="parent"), pytest.raises(ClientError):
            client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
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
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

        assert span["attributes"][SPANDATA.ERROR_TYPE] == "AccessDeniedException"
        assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 403
        assert span["attributes"][SPANDATA.AWS_REQUEST_ID] == "test-id"
        assert span["attributes"][SPANDATA.HTTP_REQUEST_RESEND_COUNT] == 1
        assert span["status"] == "error"
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="parent"), pytest.raises(ClientError):
            client.converse_stream(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                messages=[{"role": "user", "content": [{"text": "Hi"}]}],
            )

        (transaction,) = events
        (span,) = transaction["spans"]

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

        assert span["data"][SPANDATA.ERROR_TYPE] == "AccessDeniedException"
        assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 403
        assert span["data"][SPANDATA.AWS_REQUEST_ID] == "test-id"
        assert span["data"][SPANDATA.HTTP_REQUEST_RESEND_COUNT] == 1
        assert span["status"] == "internal_error"

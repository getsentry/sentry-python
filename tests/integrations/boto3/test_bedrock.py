import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.loaders import Loader
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration

pytestmark = pytest.mark.skipif(
    "bedrock-runtime" not in Loader().list_available_services("service-2"),
    reason="botocore is too old for bedrock-runtime.",
)


@pytest.fixture
def converse_response():
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": "Hello"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 50, "outputTokens": 30, "totalTokens": 80},
        "metrics": {"latencyMs": 500},
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


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_converse_stream(
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
    stubber = Stubber(client)
    stubber.add_response("converse_stream", {})

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse_stream(
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
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["attributes"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9
    else:
        events = capture_events()

        with stubber, sentry_sdk.start_transaction(name="parent"):
            client.converse_stream(
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
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MODEL]
            == "anthropic.claude-3-sonnet-20240229-v1:0"
        )

        assert span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
        assert span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.9


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
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
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
            client.converse_stream(
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
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
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

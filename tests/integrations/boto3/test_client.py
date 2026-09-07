import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3._instrumentation import (
    _get_error_attributes,
    _get_response_attributes,
    _get_server_attributes,
)
from tests.integrations.boto3.aws_mock import Body

session = boto3.Session(  # type: ignore[attr-defined]
    aws_access_key_id="-",
    aws_secret_access_key="-",
    region_name="eu-north-1",
)


def test_public_api():
    assert Boto3Integration.__module__ == "sentry_sdk.integrations.boto3"
    assert Boto3Integration.identifier == "boto3"


@pytest.fixture
def client_factory(sentry_init, monkeypatch, span_streaming):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        trace_lifecycle="stream" if span_streaming else "static",
        # avoid SDK's machine hostname being used as server name.
        server_name="",
    )
    # remove retry delay to speed up tests
    monkeypatch.setattr("botocore.endpoint.time.sleep", lambda delay: None)

    def make_client(service_name="s3", attempt_count=1, **client_kwargs):
        return session.client(
            service_name,
            config=Config(
                # `total_max_attempts` includes the initial request.
                retries={"total_max_attempts": attempt_count, "mode": "standard"}
            ),
            **client_kwargs,
        )

    return make_client


def _mock_responses(client, status_codes):
    request_span_ids = []

    def record_request(request, **kwargs):
        span = request.context.get("_sentrysdk_span")
        assert span is not None
        request_span_ids.append(span.span_id)

    def respond(request, **kwargs):
        # `request_created` runs before `before_send`, so use zero-based index for current
        # attempt; `min(..., len(status_codes) - 1)` clamps to last status to avoid `IndexError`.
        response_index = min(len(request_span_ids) - 1, len(status_codes) - 1)
        return AWSResponse(request.url, status_codes[response_index], {}, Body(b""))

    client.meta.events.register("request-created", record_request)
    client.meta.events.register("before-send", respond)
    return request_span_ids


def _capture_boto3_spans_by_op(invoke_client_method, capture_items, span_streaming):
    items = capture_items()

    if span_streaming:
        with sentry_sdk.traces.start_span(name="parent"):  # type: ignore[attr-defined]
            invoke_client_method()

        sentry_sdk.flush()
        spans = [
            item.payload
            for item in items
            if item.type == "span"
            and item.payload["attributes"].get(SPANDATA.SENTRY_ORIGIN)
            == Boto3Integration.origin
        ]
    else:
        with sentry_sdk.start_transaction():
            invoke_client_method()

        transaction = next(item.payload for item in items if item.type == "transaction")
        spans = [
            span
            for span in transaction["spans"]
            if span["origin"] == Boto3Integration.origin
        ]

    spans_by_op = {}
    for span in spans:
        op = (
            span["attributes"].get(SPANDATA.SENTRY_OP) if span_streaming else span["op"]
        )
        spans_by_op.setdefault(op, []).append(span)
    return spans_by_op


def _assert_span_finished(span, span_streaming):
    finished_timestamp = "end_timestamp" if span_streaming else "timestamp"
    assert span[finished_timestamp] is not None


def _assert_one_failed_span(spans, span_streaming):
    assert len(spans) == 1
    assert spans[0]["status"] in ("error", "internal_error")
    attributes = spans[0]["attributes"] if span_streaming else spans[0]["data"]
    assert attributes[SPANDATA.ERROR_TYPE]
    _assert_span_finished(spans[0], span_streaming)


def _capture_stubbed_client_span(
    client,
    method_name,
    api_params,
    capture_items,
    span_streaming,
    response=None,
):
    with Stubber(client) as stubber:
        stubber.add_response(method_name, response or {}, api_params)
        spans_by_op = _capture_boto3_spans_by_op(
            lambda: getattr(client, method_name)(**api_params),
            capture_items,
            span_streaming,
        )

    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])
    assert len(client_spans) == 1
    return client_spans[0]


def _span_attributes(span, span_streaming):
    return span["attributes"] if span_streaming else span["data"]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (None, {}),
        ({}, {}),
        ({"ResponseMetadata": None}, {}),
        (
            {
                "ResponseMetadata": {
                    "RequestId": "request-id",
                    "HostId": "extended-request-id",
                    "HTTPStatusCode": 200,
                    "RetryAttempts": 0,
                }
            },
            {
                SPANDATA.AWS_REQUEST_ID: "request-id",
                SPANDATA.AWS_EXTENDED_REQUEST_ID: "extended-request-id",
                SPANDATA.HTTP_STATUS_CODE: 200,
            },
        ),
        (
            {
                "ResponseMetadata": {
                    "RequestId": "request-id",
                    "HTTPStatusCode": 200,
                    "RetryAttempts": 2,
                }
            },
            {
                SPANDATA.AWS_REQUEST_ID: "request-id",
                SPANDATA.HTTP_STATUS_CODE: 200,
                "http.request.resend_count": 2,
            },
        ),
    ],
)
def test_get_response_attributes(response, expected):
    assert _get_response_attributes(response) == expected


@pytest.mark.parametrize(
    "header_name",
    ["x-amzn-requestid", "x-amzn-request-id", "x-amz-request-id"],
)
def test_get_response_attributes_reads_request_id_header(header_name):
    response = {
        "ResponseMetadata": {
            "HTTPHeaders": {header_name: "request-id"},
        }
    }

    assert _get_response_attributes(response) == {SPANDATA.AWS_REQUEST_ID: "request-id"}


def test_get_response_attributes_reads_extended_request_id_header():
    response = {
        "ResponseMetadata": {
            "HTTPHeaders": {"x-amz-id-2": "extended-request-id"},
        }
    }

    assert _get_response_attributes(response) == {
        SPANDATA.AWS_EXTENDED_REQUEST_ID: "extended-request-id"
    }


@pytest.mark.parametrize(
    ("field", "value", "attribute"),
    [
        ("RequestId", 123, SPANDATA.AWS_REQUEST_ID),
        ("RequestId", "", SPANDATA.AWS_REQUEST_ID),
        ("HTTPStatusCode", "200", SPANDATA.HTTP_STATUS_CODE),
        ("HTTPStatusCode", True, SPANDATA.HTTP_STATUS_CODE),
        ("HTTPStatusCode", 999, SPANDATA.HTTP_STATUS_CODE),
        ("RetryAttempts", "2", "http.request.resend_count"),
        ("RetryAttempts", False, "http.request.resend_count"),
        ("RetryAttempts", -1, "http.request.resend_count"),
    ],
)
def test_get_response_attributes_ignores_malformed_field(field, value, attribute):
    metadata = {
        "RequestId": "request-id",
        "HTTPStatusCode": 200,
        "RetryAttempts": 2,
    }
    metadata[field] = value

    attributes = _get_response_attributes({"ResponseMetadata": metadata})
    expected = {
        SPANDATA.AWS_REQUEST_ID: "request-id",
        SPANDATA.HTTP_STATUS_CODE: 200,
        "http.request.resend_count": 2,
    }
    expected.pop(attribute)

    # One malformed optional field must not discard other valid metadata.
    assert attributes == expected


@pytest.mark.parametrize(
    "error_response",
    [None, {"Code": ""}, {"Code": 123}],
)
def test_get_error_attributes_ignores_malformed_client_error_code(error_response):
    error = ClientError(
        {
            "Error": {"Code": "placeholder"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "HeadObject",
    )
    # `ClientError` itself expects `Error` to be a dict, so corrupt the stored
    # response afterward to exercise defensive handling of arbitrary metadata.
    error.response["Error"] = error_response

    assert _get_error_attributes(error) == {
        SPANDATA.HTTP_STATUS_CODE: 400,
        SPANDATA.ERROR_TYPE: "botocore.exceptions.ClientError",
    }


@pytest.mark.parametrize(
    (
        "service_name",
        "method_name",
        "api_params",
        "span_name",
        "rpc_method",
        "server_address",
    ),
    [
        (
            "s3",
            "head_object",
            {"Bucket": "bucket", "Key": "foo"},
            "S3.HeadObject",
            "HeadObject",
            "s3.eu-north-1.amazonaws.com",
        ),
        (
            "events",
            "list_event_buses",
            {},
            "EventBridge.ListEventBuses",
            "ListEventBuses",
            "events.eu-north-1.amazonaws.com",
        ),
    ],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_client_call_has_common_attributes(
    capture_items,
    client_factory,
    span_streaming,
    service_name,
    method_name,
    api_params,
    span_name,
    rpc_method,
    server_address,
):
    client = client_factory(service_name=service_name)
    span = _capture_stubbed_client_span(
        client,
        method_name,
        api_params,
        capture_items,
        span_streaming,
    )
    attributes = _span_attributes(span, span_streaming)

    assert span["name" if span_streaming else "description"] == span_name
    assert attributes[SPANDATA.RPC_METHOD] == rpc_method
    assert attributes[SPANDATA.RPC_SYSTEM_NAME] == "aws-api"
    assert attributes[SPANDATA.CLOUD_REGION] == "eu-north-1"
    assert attributes[SPANDATA.SERVER_ADDRESS] == server_address
    assert attributes[SPANDATA.SERVER_PORT] == 443


def test_client_call_attributes_are_available_at_span_creation(
    sentry_init, capture_items
):
    # attribute-based filtering happens during span creation, at the same boundary
    # where creation attributes are made available for sampling decisions.
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        trace_lifecycle="stream",
        ignore_spans=[
            {
                "attributes": {
                    SPANDATA.RPC_METHOD: "HeadObject",
                    SPANDATA.RPC_SYSTEM_NAME: "aws-api",
                    SPANDATA.SERVER_ADDRESS: "s3.eu-north-1.amazonaws.com",
                    SPANDATA.SERVER_PORT: 443,
                }
            }
        ],
    )
    client = session.client("s3")
    items = capture_items("span")

    with Stubber(client) as stubber:
        stubber.add_response("head_object", {}, {"Bucket": "bucket", "Key": "foo"})
        with sentry_sdk.traces.start_span(name="parent"):
            client.head_object(Bucket="bucket", Key="foo")

    sentry_sdk.flush()
    client_spans = [
        item.payload
        for item in items
        if item.payload["attributes"].get(SPANDATA.SENTRY_ORIGIN)
        == Boto3Integration.origin
    ]
    assert client_spans == []


@pytest.mark.parametrize(
    ("endpoint_url", "expected"),
    [
        (
            "http://localhost:4566",
            {
                SPANDATA.SERVER_ADDRESS: "localhost",
                SPANDATA.SERVER_PORT: 4566,
            },
        ),
        (
            "https://aws.example.test:8443",
            {
                SPANDATA.SERVER_ADDRESS: "aws.example.test",
                SPANDATA.SERVER_PORT: 8443,
            },
        ),
        (
            "https://[2001:db8::1]:9443",
            {
                SPANDATA.SERVER_ADDRESS: "2001:db8::1",
                SPANDATA.SERVER_PORT: 9443,
            },
        ),
        (None, {}),
        ("not-an-endpoint", {}),
        ("https://example.com:not-a-port", {}),
    ],
)
def test_get_server_attributes(endpoint_url, expected):
    assert _get_server_attributes(endpoint_url) == expected


@pytest.mark.parametrize("span_streaming", [True, False])
def test_client_call_omits_missing_region(
    capture_items,
    client_factory,
    monkeypatch,
    span_streaming,
):
    client = client_factory()
    monkeypatch.setattr(client.meta.config, "region_name", None)

    span = _capture_stubbed_client_span(
        client,
        "head_object",
        {"Bucket": "bucket", "Key": "foo"},
        capture_items,
        span_streaming,
    )

    assert SPANDATA.CLOUD_REGION not in _span_attributes(span, span_streaming)


@pytest.mark.parametrize("span_streaming", [True, False])
def test_client_call_has_response_attributes(
    capture_items,
    client_factory,
    span_streaming,
):
    client = client_factory()
    span = _capture_stubbed_client_span(
        client,
        "head_object",
        {"Bucket": "bucket", "Key": "foo"},
        capture_items,
        span_streaming,
        response={
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-id",
                "HostId": "extended-request-id",
                "RetryAttempts": 0,
            }
        },
    )
    attributes = _span_attributes(span, span_streaming)

    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 200
    assert attributes[SPANDATA.AWS_REQUEST_ID] == "request-id"
    assert attributes[SPANDATA.AWS_EXTENDED_REQUEST_ID] == "extended-request-id"
    assert "http.request.resend_count" not in attributes
    assert SPANDATA.ERROR_TYPE not in attributes


@pytest.mark.parametrize("span_streaming", [True, False])
def test_retry_attempts_share_one_client_span(
    capture_items,
    client_factory,
    span_streaming,
):
    attempt_count = 3
    client = client_factory(attempt_count=attempt_count)
    request_span_ids = _mock_responses(client, [500] * (attempt_count - 1) + [200])

    spans_by_op = _capture_boto3_spans_by_op(
        lambda: client.head_object(Bucket="bucket", Key="foo"),
        capture_items,
        span_streaming,
    )
    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])

    assert len(request_span_ids) == attempt_count
    # all `AWSRequest` instances created during retries reference the same client span.
    assert len(set(request_span_ids)) == 1
    assert len(client_spans) == 1
    attributes = _span_attributes(client_spans[0], span_streaming)
    assert attributes["http.request.resend_count"] == attempt_count - 1


@pytest.mark.parametrize("span_streaming", [True, False])
def test_retries_exhausted_has_one_failed_client_span(
    capture_items,
    client_factory,
    span_streaming,
):
    client = client_factory(attempt_count=2)
    request_span_ids = _mock_responses(client, [500])

    def attempt_failed_head_object_call():
        with pytest.raises(ClientError):
            client.head_object(Bucket="bucket", Key="foo.pdf")

    spans_by_op = _capture_boto3_spans_by_op(
        attempt_failed_head_object_call, capture_items, span_streaming
    )
    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])

    assert len(request_span_ids) == 2
    assert len(set(request_span_ids)) == 1
    _assert_one_failed_span(client_spans, span_streaming)
    attributes = _span_attributes(client_spans[0], span_streaming)
    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 500
    assert attributes["http.request.resend_count"] == 1


@pytest.mark.parametrize("span_streaming", [True, False])
def test_client_error_has_response_attributes_and_is_unchanged(
    capture_items,
    client_factory,
    span_streaming,
):
    client = client_factory()
    original_exception = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "must not become a span attribute",
            },
            "ResponseMetadata": {
                "RequestId": "request-id",
                "HTTPStatusCode": 403,
                "RetryAttempts": 1,
            },
        },
        "HeadObject",
    )

    def raise_client_error(**kwargs):
        raise original_exception

    client.meta.events.register("before-parameter-build", raise_client_error)

    def invoke_failing_client_method():
        with pytest.raises(ClientError) as exc_info:
            client.head_object(Bucket="bucket", Key="foo")
        assert exc_info.value is original_exception

    spans_by_op = _capture_boto3_spans_by_op(
        invoke_failing_client_method, capture_items, span_streaming
    )
    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])
    _assert_one_failed_span(client_spans, span_streaming)
    attributes = _span_attributes(client_spans[0], span_streaming)

    assert attributes[SPANDATA.AWS_REQUEST_ID] == "request-id"
    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 403
    assert attributes["http.request.resend_count"] == 1
    assert attributes[SPANDATA.ERROR_TYPE] == "AccessDeniedException"
    assert "Error.Message" not in attributes
    assert "exception.message" not in attributes
    assert "error.message" not in attributes


@pytest.mark.parametrize(
    "event_name",
    [
        pytest.param("before-parameter-build"),
        pytest.param("before-send"),
    ],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_client_call_exception_is_unchanged_and_finishes_span(
    capture_items,
    client_factory,
    span_streaming,
    event_name,
):
    client = client_factory()
    if event_name == "before-send":
        original_exception = EndpointConnectionError(
            endpoint_url="https://s3.eu-north-1.amazonaws.com"
        )
    else:
        original_exception = ValueError("parameter processing failed")

    def raise_original_exception(**kwargs):
        raise original_exception

    client.meta.events.register(event_name, raise_original_exception)

    def invoke_failing_client_method():
        with pytest.raises(type(original_exception)) as exc_info:
            client.head_object(Bucket="bucket", Key="foo")
        assert exc_info.value is original_exception

    spans_by_op = _capture_boto3_spans_by_op(
        invoke_failing_client_method, capture_items, span_streaming
    )
    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])
    _assert_one_failed_span(client_spans, span_streaming)

    attributes = _span_attributes(client_spans[0], span_streaming)
    expected_error_type = (
        "botocore.exceptions.EndpointConnectionError"
        if event_name == "before-send"
        else "ValueError"
    )
    assert attributes[SPANDATA.ERROR_TYPE] == expected_error_type


@pytest.mark.tests_internal_exceptions
@pytest.mark.parametrize("span_streaming", [True, False])
def test_response_attribute_extraction_failure_does_not_change_response(
    capture_items,
    client_factory,
    monkeypatch,
    span_streaming,
):
    client = client_factory()
    api_params = {"Bucket": "bucket", "Key": "foo"}
    original_response = {
        "ResponseMetadata": {
            "HTTPStatusCode": 200,
        }
    }
    returned_responses = []

    def fail_attribute_extraction(response):
        raise RuntimeError("attribute extraction failed")

    monkeypatch.setattr(
        "sentry_sdk.integrations.boto3._instrumentation._get_response_attributes",
        fail_attribute_extraction,
    )

    def invoke_client_method():
        returned_responses.append(client.head_object(**api_params))

    with Stubber(client) as stubber:
        stubber.add_response("head_object", original_response, api_params)
        spans_by_op = _capture_boto3_spans_by_op(
            invoke_client_method, capture_items, span_streaming
        )

    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])
    assert len(returned_responses) == 1
    assert returned_responses[0] is original_response
    assert len(client_spans) == 1
    _assert_span_finished(client_spans[0], span_streaming)


@pytest.mark.tests_internal_exceptions
@pytest.mark.parametrize("span_streaming", [True, False])
def test_error_attribute_extraction_failure_does_not_replace_original_exception(
    capture_items,
    client_factory,
    monkeypatch,
    span_streaming,
):
    client = client_factory()
    original_exception = ValueError("parameter processing failed")

    def raise_original_exception(**kwargs):
        raise original_exception

    def fail_attribute_extraction(exception):
        raise RuntimeError("attribute extraction failed")

    client.meta.events.register("before-parameter-build", raise_original_exception)
    monkeypatch.setattr(
        "sentry_sdk.integrations.boto3._instrumentation._get_error_attributes",
        fail_attribute_extraction,
    )

    def invoke_failing_client_method():
        with pytest.raises(ValueError) as exc_info:
            client.head_object(Bucket="bucket", Key="foo")
        assert exc_info.value is original_exception

    spans_by_op = _capture_boto3_spans_by_op(
        invoke_failing_client_method, capture_items, span_streaming
    )
    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])

    assert len(client_spans) == 1
    assert client_spans[0]["status"] in ("error", "internal_error")
    _assert_span_finished(client_spans[0], span_streaming)


@pytest.mark.parametrize("span_streaming", [True, False])
def test_streaming_response_attributes_belong_to_client_span(
    capture_items,
    client_factory,
    span_streaming,
):
    client = client_factory()

    def respond(request, **kwargs):
        return AWSResponse(
            request.url,
            200,
            {
                "content-length": "5",
                "x-amz-request-id": "request-id",
            },
            Body(b"hello"),
        )

    client.meta.events.register("before-send", respond)

    def invoke_client_method_and_read_body():
        body = client.get_object(Bucket="bucket", Key="foo")["Body"]
        assert body.read() == b"hello"
        assert body.read() == b""

    spans_by_op = _capture_boto3_spans_by_op(
        invoke_client_method_and_read_body, capture_items, span_streaming
    )
    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])
    stream_spans = spans_by_op.get(OP.HTTP_CLIENT_STREAM, [])

    assert len(client_spans) == 1
    assert len(stream_spans) == 1
    client_attributes = _span_attributes(client_spans[0], span_streaming)
    stream_attributes = _span_attributes(stream_spans[0], span_streaming)
    assert client_attributes[SPANDATA.AWS_REQUEST_ID] == "request-id"
    assert client_attributes[SPANDATA.HTTP_STATUS_CODE] == 200
    assert "http.request.resend_count" not in client_attributes
    assert SPANDATA.AWS_REQUEST_ID not in stream_attributes
    assert SPANDATA.HTTP_STATUS_CODE not in stream_attributes


@pytest.mark.parametrize("span_streaming", [True, False])
def test_streaming_body_read_failure_finishes_stream_span(
    capture_items,
    client_factory,
    span_streaming,
):
    client = client_factory()
    original_exception = OSError("stream read failed")

    class _FailingBody(Body):
        def __init__(self, exception):
            super().__init__(b"")
            self._exception = exception

        def read(self, *args, **kwargs):
            raise self._exception

    def respond(request, **kwargs):
        return AWSResponse(
            request.url,
            200,
            {"content-length": "1"},
            _FailingBody(original_exception),
        )

    client.meta.events.register("before-send", respond)

    def invoke_client_method_and_read_body():
        body = client.get_object(Bucket="bucket", Key="foo")["Body"]
        with pytest.raises(OSError) as exc_info:
            body.read()
        assert exc_info.value is original_exception

    spans_by_op = _capture_boto3_spans_by_op(
        invoke_client_method_and_read_body, capture_items, span_streaming
    )
    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])
    stream_spans = spans_by_op.get(OP.HTTP_CLIENT_STREAM, [])

    assert len(client_spans) == 1
    _assert_one_failed_span(stream_spans, span_streaming)
    attributes = _span_attributes(stream_spans[0], span_streaming)
    assert attributes[SPANDATA.ERROR_TYPE] == "OSError"

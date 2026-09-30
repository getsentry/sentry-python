from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._services.registry import _SERVICE_EXTENSIONS
from sentry_sdk.integrations.boto3.consts import AWS_RPC_SYSTEM_NAME, ORIGIN
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import Span
from tests.integrations.boto3.aws_mock import Body, MockResponse

session = boto3.Session(  # type: ignore[attr-defined]
    aws_access_key_id="-",
    aws_secret_access_key="-",
    region_name="eu-north-1",
)


@pytest.fixture
def streaming_s3_server():
    class StreamingS3Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(b"x")
            self.wfile.flush()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), StreamingS3Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "consume",
    ["read", "read_exact", "context", "close"],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_streaming_span_order_and_scope(
    sentry_init,
    capture_items,
    streaming_s3_server,
    consume,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
        default_integrations=False,
        integrations=[Boto3Integration(), StdlibIntegration()],
        server_name="",
    )
    server = streaming_s3_server
    client = session.client(
        "s3",
        endpoint_url="http://127.0.0.1:%s" % server.server_port,
        config=Config(
            retries={"total_max_attempts": 1, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )
    request_client_spans = []

    def record_client_span(request, **kwargs):
        request_client_spans.append(request.context["_sentrysdk_span"])

    client.meta.events.register("request-created", record_client_span)
    items = capture_items()

    parent_context = (
        sentry_sdk.traces.start_span(name="parent")  # type: ignore[attr-defined]
        if span_streaming
        else sentry_sdk.start_transaction(name="parent")
    )
    with parent_context as parent:
        body = client.get_object(Bucket="bucket", Key="key")["Body"]
        assert len(request_client_spans) == 1
        request_client_span = request_client_spans[0]
        if span_streaming:
            assert isinstance(request_client_span, StreamedSpan)
            assert request_client_span.end_timestamp is None
            assert sentry_sdk.traces.get_current_span() is parent  # type: ignore[attr-defined]
        else:
            assert isinstance(request_client_span, Span)
            assert not isinstance(request_client_span, StreamedSpan)
            assert request_client_span.timestamp is None
            assert sentry_sdk.get_current_span() is parent

        if consume == "read":
            assert body.read() == b"x"
        elif consume == "read_exact":
            assert body.read(1) == b"x"
        elif consume == "context":
            if not hasattr(body, "__enter__"):
                body.close()
                pytest.skip("`StreamingBody` context manager is unavailable.")
            with body as raw_stream:
                assert raw_stream.read() == b"x"
        else:
            body.close()

        if span_streaming:
            assert request_client_span.end_timestamp is not None
            assert sentry_sdk.traces.get_current_span() is parent  # type: ignore[attr-defined]

            probe = sentry_sdk.traces.start_span(name="probe")  # type: ignore[attr-defined]
            assert probe._parent_span_id == parent.span_id
            probe.end()

            body.close()
            assert sentry_sdk.traces.get_current_span() is parent  # type: ignore[attr-defined]
        else:
            assert request_client_span.timestamp is not None
            assert sentry_sdk.get_current_span() is parent

    sentry_sdk.flush()
    if span_streaming:
        spans = [item.payload for item in items]
    else:
        transaction = next(item.payload for item in items if item.type == "transaction")
        spans = transaction["spans"]
    client_spans = [
        span
        for span in spans
        if span.get("name", span.get("description")) == "aws.s3.GetObject"
        and (
            span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == ORIGIN
            and span["attributes"].get(SPANDATA.SENTRY_OP) == OP.HTTP_CLIENT
            if span_streaming
            else span["origin"] == ORIGIN and span["op"] == OP.HTTP_CLIENT
        )
    ]
    http_spans = [
        span
        for span in spans
        if (
            span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == "auto.http.stdlib.httplib"
            if span_streaming
            else span["origin"] == "auto.http.stdlib.httplib"
        )
    ]
    stream_spans = [
        span
        for span in spans
        if span.get("name", span.get("description")) == "aws.s3.GetObject"
        and (
            span["attributes"].get(SPANDATA.SENTRY_OP) == OP.HTTP_CLIENT_STREAM
            if span_streaming
            else span["op"] == OP.HTTP_CLIENT_STREAM
        )
    ]
    assert len(client_spans) == 1
    assert len(http_spans) == 1
    assert len(stream_spans) == 1
    client_span = client_spans[0]
    http_span = http_spans[0]
    stream_span = stream_spans[0]

    assert http_span["parent_span_id"] == client_span["span_id"]
    assert stream_span["parent_span_id"] == client_span["span_id"]
    assert client_span["span_id"] == request_client_span.span_id
    end_timestamp = "end_timestamp" if span_streaming else "timestamp"
    for span in (client_span, http_span, stream_span):
        assert span[end_timestamp] is not None


def test_non_body_stream_does_not_delay_client_span(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        integrations=[Boto3Integration()],
        server_name="",
    )
    client = session.client("lambda")

    def respond(request, **kwargs):
        return AWSResponse(
            request.url,
            200,
            {"content-length": "1"},
            Body(b"x"),
        )

    client.meta.events.register("before-send", respond)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="parent") as parent:  # type: ignore[attr-defined]
        response = client.invoke(FunctionName="function")
        assert isinstance(response["Payload"], StreamingBody)
        assert sentry_sdk.traces.get_current_span() is parent  # type: ignore[attr-defined]

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    boto_spans = [
        span
        for span in spans
        if span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == ORIGIN
    ]
    assert len(boto_spans) == 1
    assert boto_spans[0]["attributes"].get(SPANDATA.SENTRY_OP) == OP.HTTP_CLIENT
    response["Payload"].close()


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


def _capture_boto3_spans_by_op(
    invoke_client_method,
    capture_items,
    span_streaming,
    expected_origin=ORIGIN,
):
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
            == expected_origin
        ]
    else:
        with sentry_sdk.start_transaction():
            invoke_client_method()

        transaction = next(item.payload for item in items if item.type == "transaction")
        spans = [
            span for span in transaction["spans"] if span["origin"] == expected_origin
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
    assert _span_attributes(spans[0], span_streaming)[SPANDATA.ERROR_TYPE]
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
        stubber.add_response(
            method_name, response if response is not None else {}, api_params
        )
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_service_extension_customizes_client_span(
    capture_items,
    client_factory,
    monkeypatch,
    span_streaming,
):
    class TestServiceExtension(_ServiceExtension):
        def get_span_op(self, ctx):
            return "aws.test"

        def get_span_origin(self, ctx):
            return "auto.aws.test"

        def get_request_attributes(self, ctx):
            return {
                "aws.test.request": ctx.params["Key"],
                SPANDATA.SENTRY_KIND: "producer",
            }

        def get_response_attributes(self, ctx, response):
            return {
                "aws.test.response": response["ResponseMetadata"]["RequestId"],
                SPANDATA.HTTP_STATUS_CODE: 418,
            }

    monkeypatch.setitem(_SERVICE_EXTENSIONS, "s3", TestServiceExtension())
    client = client_factory()
    api_params = {"Bucket": "bucket", "Key": "foo"}

    with Stubber(client) as stubber:
        stubber.add_response(
            "head_object",
            {
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-id",
                    "HostId": "extended-request-id",
                }
            },
            api_params,
        )
        spans_by_op = _capture_boto3_spans_by_op(
            lambda: client.head_object(**api_params),
            capture_items,
            span_streaming,
            expected_origin="auto.aws.test",
        )

    spans = spans_by_op.get("aws.test", [])
    assert len(spans) == 1
    attributes = _span_attributes(spans[0], span_streaming)
    assert attributes["aws.test.request"] == "foo"
    assert attributes["aws.test.response"] == "request-id"
    assert attributes[SPANDATA.SENTRY_KIND] == "producer"
    assert attributes[SPANDATA.RPC_METHOD] == "HeadObject"
    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 200
    assert attributes[SPANDATA.AWS_EXTENDED_REQUEST_ID] == "extended-request-id"
    _assert_span_finished(spans[0], span_streaming)
    if span_streaming:
        assert attributes[SPANDATA.SENTRY_ORIGIN] == "auto.aws.test"
    else:
        assert spans[0]["origin"] == "auto.aws.test"


@pytest.mark.parametrize(
    (
        "service_name",
        "method_name",
        "api_params",
        "span_name",
        "rpc_service",
        "rpc_method",
        "endpoint_url",
        "server_address",
        "server_port",
    ),
    [
        (
            "s3",
            "head_object",
            {"Bucket": "bucket", "Key": "foo"},
            "aws.s3.HeadObject",
            "S3",
            "HeadObject",
            "http://localhost:4566",
            "localhost",
            4566,
        ),
        (
            "events",
            "list_event_buses",
            {},
            "aws.eventbridge.ListEventBuses",
            "EventBridge",
            "ListEventBuses",
            None,
            "events.eu-north-1.amazonaws.com",
            443,
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
    rpc_service,
    rpc_method,
    endpoint_url,
    server_address,
    server_port,
):
    client = client_factory(service_name=service_name, endpoint_url=endpoint_url)
    span = _capture_stubbed_client_span(
        client,
        method_name,
        api_params,
        capture_items,
        span_streaming,
        response={
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-id",
                "RetryAttempts": 0,
            }
        },
    )
    attributes = _span_attributes(span, span_streaming)

    assert span["name" if span_streaming else "description"] == span_name
    assert attributes[SPANDATA.RPC_SERVICE] == rpc_service
    assert attributes[SPANDATA.RPC_METHOD] == rpc_method
    assert attributes[SPANDATA.RPC_SYSTEM_NAME] == AWS_RPC_SYSTEM_NAME
    assert attributes[SPANDATA.SENTRY_KIND] == "client"
    assert attributes[SPANDATA.CLOUD_REGION] == "eu-north-1"
    assert attributes[SPANDATA.SERVER_ADDRESS] == server_address
    assert attributes[SPANDATA.SERVER_PORT] == server_port
    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 200
    assert attributes[SPANDATA.AWS_REQUEST_ID] == "request-id"
    assert SPANDATA.HTTP_REQUEST_RESEND_COUNT not in attributes
    assert SPANDATA.ERROR_TYPE not in attributes
    _assert_span_finished(span, span_streaming)


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
                    SPANDATA.RPC_SERVICE: "S3",
                    SPANDATA.RPC_SYSTEM_NAME: AWS_RPC_SYSTEM_NAME,
                    SPANDATA.SERVER_ADDRESS: "s3.eu-north-1.amazonaws.com",
                    SPANDATA.SERVER_PORT: 443,
                }
            }
        ],
    )
    client = session.client("s3")
    items = capture_items("span")

    with MockResponse(client, 200, {}, b""):
        with sentry_sdk.traces.start_span(name="parent") as parent:
            response = client.head_object(Bucket="bucket", Key="foo")
            assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
            assert sentry_sdk.traces.get_current_span() is parent
            assert SPANDATA.RPC_METHOD not in parent.get_attributes()
            assert SPANDATA.HTTP_REQUEST_METHOD not in parent.get_attributes()

    sentry_sdk.flush()
    client_spans = [
        item.payload
        for item in items
        if item.payload["attributes"].get(SPANDATA.SENTRY_ORIGIN) == ORIGIN
    ]
    assert client_spans == []


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "request_id_header", ["x-amzn-requestid", "x-amzn-request-id", "x-amz-request-id"]
)
def test_client_call_has_response_header_attributes(
    capture_items, client_factory, span_streaming, request_id_header
):
    client = client_factory()
    headers = {request_id_header: "request-id", "x-amz-id-2": "extended-request-id"}
    with MockResponse(client, 200, headers, b""):
        spans_by_op = _capture_boto3_spans_by_op(
            lambda: client.head_object(Bucket="bucket", Key="foo"),
            capture_items,
            span_streaming,
        )

    spans = spans_by_op[OP.HTTP_CLIENT]
    assert len(spans) == 1
    attributes = _span_attributes(spans[0], span_streaming)
    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 200
    assert attributes[SPANDATA.AWS_REQUEST_ID] == "request-id"
    assert attributes[SPANDATA.AWS_EXTENDED_REQUEST_ID] == "extended-request-id"
    assert SPANDATA.HTTP_REQUEST_RESEND_COUNT not in attributes


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
    assert attributes[SPANDATA.HTTP_REQUEST_RESEND_COUNT] == attempt_count - 1


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
    assert attributes[SPANDATA.HTTP_REQUEST_RESEND_COUNT] == 1


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("with_service_extension", [False, True])
def test_client_error_has_response_attributes_and_is_unchanged(
    capture_items,
    client_factory,
    monkeypatch,
    span_streaming,
    with_service_extension,
):
    class TestServiceExtension(_ServiceExtension):
        def get_response_attributes(self, ctx, response):
            return {
                "aws.test.error": response["Error"]["Code"],
                SPANDATA.ERROR_TYPE: "must-not-override",
                SPANDATA.HTTP_STATUS_CODE: 418,
            }

    if with_service_extension:
        monkeypatch.setitem(_SERVICE_EXTENSIONS, "s3", TestServiceExtension())
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
    assert attributes[SPANDATA.HTTP_REQUEST_RESEND_COUNT] == 1
    assert attributes[SPANDATA.ERROR_TYPE] == "AccessDeniedException"
    if with_service_extension:
        assert attributes["aws.test.error"] == "AccessDeniedException"
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
@pytest.mark.parametrize(
    "failing_instrumentation",
    [
        "_start_client_span",
        "_get_response_attributes",
    ],
)
def test_instrumentation_failure_does_not_change_response(
    capture_items,
    client_factory,
    monkeypatch,
    span_streaming,
    failing_instrumentation,
):
    client = client_factory()
    api_params = {"Bucket": "bucket", "Key": "foo"}
    original_response = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    returned_responses = []

    def fail_instrumentation(*args, **kwargs):
        raise RuntimeError("instrumentation failed")

    monkeypatch.setattr(
        f"sentry_sdk.integrations.boto3._client.{failing_instrumentation}",
        fail_instrumentation,
    )

    def invoke_client_method():
        returned_responses.append(client.head_object(**api_params))

    with Stubber(client) as stubber:
        stubber.add_response("head_object", original_response, api_params)
        spans_by_op = _capture_boto3_spans_by_op(
            invoke_client_method, capture_items, span_streaming
        )

    client_spans = spans_by_op.get(OP.HTTP_CLIENT, [])
    assert returned_responses == [original_response]
    assert returned_responses[0] is original_response
    if failing_instrumentation == "_get_response_attributes":
        assert len(client_spans) == 1
        _assert_span_finished(client_spans[0], span_streaming)
    else:
        assert client_spans == []


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
        "sentry_sdk.integrations.boto3._client._get_error_attributes",
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
    assert SPANDATA.HTTP_REQUEST_RESEND_COUNT not in client_attributes
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
            # urllib3 closes the response before propagating some read failures.
            self.close()
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
    _assert_one_failed_span(client_spans, span_streaming)
    _assert_one_failed_span(stream_spans, span_streaming)
    attributes = _span_attributes(stream_spans[0], span_streaming)
    assert attributes[SPANDATA.ERROR_TYPE] == "OSError"

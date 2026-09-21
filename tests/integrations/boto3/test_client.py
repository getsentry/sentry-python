from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.response import StreamingBody

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.stdlib import StdlibIntegration
from tests.integrations.boto3.aws_mock import Body

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


def test_public_api():
    assert Boto3Integration.__module__ == "sentry_sdk.integrations.boto3"
    assert Boto3Integration.identifier == "boto3"


@pytest.mark.parametrize(
    "consume",
    ["read", "read_exact", "context", "close"],
)
def test_streaming_span_order_and_scope(
    sentry_init,
    capture_items,
    streaming_s3_server,
    consume,
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="parent") as parent:  # type: ignore[attr-defined]
        body = client.get_object(Bucket="bucket", Key="key")["Body"]
        assert sentry_sdk.traces.get_current_span() is parent  # type: ignore[attr-defined]

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

        assert sentry_sdk.traces.get_current_span() is parent  # type: ignore[attr-defined]

        probe = sentry_sdk.traces.start_span(name="probe")  # type: ignore[attr-defined]
        assert probe._parent_span_id == parent.span_id
        probe.end()

        body.close()
        assert sentry_sdk.traces.get_current_span() is parent  # type: ignore[attr-defined]

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    client_spans = [
        span
        for span in spans
        if span["name"] == "aws.s3.GetObject"
        and span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == Boto3Integration.origin
        and span["attributes"].get(SPANDATA.SENTRY_OP) == OP.HTTP_CLIENT
    ]
    http_spans = [
        span
        for span in spans
        if span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == "auto.http.stdlib.httplib"
    ]
    stream_spans = [
        span
        for span in spans
        if span["name"] == "aws.s3.GetObject"
        and span["attributes"].get(SPANDATA.SENTRY_OP) == OP.HTTP_CLIENT_STREAM
    ]
    assert len(client_spans) == 1
    assert len(http_spans) == 1
    assert len(stream_spans) == 1
    client_span = client_spans[0]
    http_span = http_spans[0]
    stream_span = stream_spans[0]

    assert http_span["parent_span_id"] == client_span["span_id"]
    assert stream_span["parent_span_id"] == client_span["span_id"]
    assert client_span["start_timestamp"] <= http_span["start_timestamp"]
    assert http_span["start_timestamp"] <= stream_span["start_timestamp"]
    assert http_span["end_timestamp"] <= stream_span["end_timestamp"]
    assert stream_span["end_timestamp"] <= client_span["end_timestamp"]


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
        if span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == Boto3Integration.origin
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
    _assert_span_finished(spans[0], span_streaming)


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
    if span_streaming:
        _assert_one_failed_span(client_spans, span_streaming=True)
    _assert_one_failed_span(stream_spans, span_streaming)

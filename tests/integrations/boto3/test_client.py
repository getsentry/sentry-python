import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.config import Config

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.boto3 import Boto3Integration
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
            and item.payload["attributes"].get("sentry.origin")
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
            span["attributes"].get("sentry.op") if span_streaming else span["op"]
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

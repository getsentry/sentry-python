import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.config import Config
from botocore.exceptions import ClientError

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from tests.integrations.boto3.aws_mock import Body

session = boto3.Session(  # type: ignore[attr-defined]
    aws_access_key_id="-",
    aws_secret_access_key="-",
    region_name="eu-north-1",
)


@pytest.fixture
def client_factory(sentry_init, monkeypatch, span_streaming):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        trace_lifecycle="stream" if span_streaming else "static",
    )
    # remove retry delay to speed up tests
    monkeypatch.setattr("botocore.endpoint.time.sleep", lambda delay: None)

    def make_client(attempt_count=1):
        return session.client(
            "s3",
            config=Config(
                # `total_max_attempts` is the total number of retries, including the initial request.
                retries={"total_max_attempts": attempt_count, "mode": "standard"}
            ),
        )

    return make_client


def _mock_responses(client, status_codes):
    requests_created = []
    span_ids = []

    def record_request(request, **kwargs):
        requests_created.append(request)
        span = sentry_sdk.traces.get_current_span()  # type: ignore[attr-defined]
        assert span is not None
        span_ids.append(span.span_id)

    def respond(request, **kwargs):
        # `request_created` runs before `before_send`, so use zero-based index for current
        # attempt; `min(..., len(status_codes) - 1)` clamps to last status to avoid `IndexError`.
        response_index = min(len(requests_created) - 1, len(status_codes) - 1)
        return AWSResponse(request.url, status_codes[response_index], {}, Body(b""))

    client.meta.events.register("request-created", record_request)
    client.meta.events.register("before-send", respond)
    return requests_created, span_ids


def _capture_client_spans(call, capture_items, span_streaming):
    items = capture_items()

    if span_streaming:
        with sentry_sdk.traces.start_span(name="parent"):  # type: ignore[attr-defined]
            call()

        sentry_sdk.flush()
        return [
            item.payload
            for item in items
            if item.type == "span"
            and item.payload["attributes"].get(SPANDATA.SENTRY_OP) == OP.HTTP_CLIENT
            and item.payload["attributes"].get(SPANDATA.SENTRY_ORIGIN)
            == Boto3Integration.origin
        ]

    with sentry_sdk.start_transaction():
        call()

    transaction = next(item.payload for item in items if item.type == "transaction")
    return [
        span
        for span in transaction["spans"]
        if span["op"] == OP.HTTP_CLIENT
        and span[SPANDATA.SENTRY_ORIGIN] == Boto3Integration.origin
    ]


@pytest.mark.parametrize("attempt_count", [2, 3])
@pytest.mark.parametrize("span_streaming", [True, False])
def test_retry_attempts_share_one_client_span(
    capture_items,
    client_factory,
    span_streaming,
    attempt_count,
):
    client = client_factory(attempt_count=attempt_count)
    requests_created, span_ids = _mock_responses(
        client, [500] * (attempt_count - 1) + [200]
    )

    client_spans = _capture_client_spans(
        lambda: client.head_object(Bucket="bucket", Key="foo"),
        capture_items,
        span_streaming,
    )

    assert len(requests_created) == attempt_count
    assert len(span_ids) == attempt_count
    # all `AWSRequest` instances created during retries reference the same client span.
    assert len(set(span_ids)) == 1
    assert len(client_spans) == 1


@pytest.mark.parametrize("span_streaming", [True, False])
def test_retries_exhausted_has_one_failed_client_span(
    capture_items,
    client_factory,
    span_streaming,
):
    client = client_factory()
    requests_created, span_ids = _mock_responses(client, [500])

    def attempt_failed_head_object_call():
        with pytest.raises(ClientError):
            client.head_object(Bucket="bucket", Key="foo.pdf")

    client_spans = _capture_client_spans(
        attempt_failed_head_object_call, capture_items, span_streaming
    )

    assert len(requests_created) == 2
    assert len(span_ids) == 2
    assert len(set(span_ids)) == 1
    assert len(client_spans) == 1

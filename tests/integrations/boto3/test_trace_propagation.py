import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.auth import SIGNED_HEADERS_BLACKLIST
from botocore.config import Config
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.utils import (
    _get_aws_sigv4_signed_headers_from_authorization_header,
    _get_aws_sigv4_signed_headers_from_url_query_string,
)
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)

# botocore 1.43.115+ never signs `baggage`.
BOTOCORE_SIGNS_BAGGAGE = "baggage" not in SIGNED_HEADERS_BLACKLIST


class _AwsRequestHandler(BaseHTTPRequestHandler):
    requests = []

    def do_HEAD(self):
        self.__class__.requests.append(self.headers)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def _start_server():
    _AwsRequestHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _AwsRequestHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_botocore_merges_propagation_before_sigv4_signing(
    sentry_init,
):
    sentry_init(
        traces_sample_rate=1.0,
        default_integrations=False,
        integrations=[Boto3Integration(), StdlibIntegration()],
    )

    server, thread = _start_server()

    try:
        client = boto3.client(  # type: ignore[attr-defined]
            "s3",
            # connect to mock AWS server.
            endpoint_url=f"http://127.0.0.1:{server.server_port}",
            aws_access_key_id="test-access-key",
            aws_secret_access_key="test-secret-key",
            config=Config(signature_version="v4"),
        )

        def _inject_third_party_baggage(request, **kwargs):
            request.headers.add_header(
                "baggage",
                "dd-origin=synthetics,sentry-trace_id=stale,sentry-sample_rand=0.100000",
            )
            request.headers.add_header("baggage", "vendor=value")

        signed_request_headers = {}

        def capture_headers_after_instrumentation(request, **kwargs):
            for header_name in ("baggage", "sentry-trace"):
                signed_request_headers[header_name] = request.headers.get_all(
                    header_name
                )

        # inject third-party and stale Sentry members before Sentry merges them.
        client.meta.events.register("before-sign", _inject_third_party_baggage)
        client.meta.events.register_last(
            "before-sign", capture_headers_after_instrumentation
        )
        with sentry_sdk.start_span(  # type: ignore[attr-defined]
            name="incoming"
        ):
            response = client.head_object(
                Bucket="example-bucket",
                Key="example-key",
            )

        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
        headers = _AwsRequestHandler.requests[-1]

        baggage_headers = headers.get_all("baggage")
        assert baggage_headers is not None
        assert len(baggage_headers) == 1
        assert baggage_headers == signed_request_headers["baggage"]

        baggage = baggage_headers[0]
        # third-party `baggage` members: leave as-is.
        assert "dd-origin=synthetics" in baggage
        assert "vendor=value" in baggage
        # stale Sentry `baggage` members: replace with current values.
        assert "sentry-trace_id=" in baggage
        assert "sentry-trace_id=stale" not in baggage
        # each Sentry `baggage` member appears once after merge.
        assert baggage.count("sentry-trace_id=") == 1
        assert baggage.count("sentry-sample_rand=") == 1

        # `sentry-trace`: one value.
        sentry_trace_headers = headers.get_all("sentry-trace")
        assert sentry_trace_headers is not None
        assert len(sentry_trace_headers) == 1
        assert sentry_trace_headers == signed_request_headers["sentry-trace"]
        # final `sentry-trace` and `baggage` are named in SignedHeaders.
        signed_headers = _get_aws_sigv4_signed_headers_from_authorization_header(
            headers.get("Authorization", "")
        )
        assert "sentry-trace" in signed_headers
        assert ("baggage" in signed_headers) == BOTOCORE_SIGNS_BAGGAGE
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_botocore_without_boto3_integration_preserves_signed_baggage(
    sentry_init,
):
    """Leave signed `baggage` as-is; add unsigned `sentry-trace`."""
    sentry_init(
        traces_sample_rate=1.0,
        default_integrations=False,
        integrations=[StdlibIntegration()],
    )

    server, thread = _start_server()
    try:
        client = boto3.client(  # type: ignore[attr-defined]
            "s3",
            endpoint_url=f"http://127.0.0.1:{server.server_port}",
            aws_access_key_id="test-access-key",
            aws_secret_access_key="test-secret-key",
            config=Config(signature_version="v4"),
        )

        def _inject_signed_baggage(request, **kwargs):
            request.headers.add_header("baggage", "vendor=value")

        # inject `baggage` before SigV4; stdlib-only path cannot change signed fields.
        client.meta.events.register("before-sign", _inject_signed_baggage)
        with sentry_sdk.start_span(  # type: ignore[attr-defined]
            name="incoming"
        ):
            response = client.head_object(
                Bucket="example-bucket",
                Key="example-key",
            )

        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
        headers = _AwsRequestHandler.requests[-1]
        # unsigned `sentry-trace`: add it.
        assert len(headers.get_all("sentry-trace")) == 1
        signed_headers = _get_aws_sigv4_signed_headers_from_authorization_header(
            headers.get("Authorization", "")
        )
        assert "sentry-trace" not in signed_headers
        if BOTOCORE_SIGNS_BAGGAGE:
            # signed `baggage`: leave as-is.
            assert headers.get_all("baggage") == ["vendor=value"]
            assert "baggage" in signed_headers
        else:
            # unsigned `baggage`: append Sentry members as a separate field.
            vendor_baggage, sentry_baggage = headers.get_all("baggage")
            assert vendor_baggage == "vendor=value"
            assert sentry_baggage.startswith("sentry-trace_id=")
            assert "baggage" not in signed_headers
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_presigned_urls_do_not_require_sentry_headers(sentry_init):
    """Do not add or sign `sentry-trace` or `baggage` on a presigned URL."""
    sentry_init(
        traces_sample_rate=1.0,
        default_integrations=False,
        integrations=[Boto3Integration(), StdlibIntegration()],
    )
    client = boto3.client(  # type: ignore[attr-defined]
        "s3",
        aws_access_key_id="test-access-key",
        aws_secret_access_key="test-secret-key",
        config=Config(signature_version="s3v4"),
    )

    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": "example-bucket", "Key": "example-key"},
        ExpiresIn=60,
    )
    query = parse_qs(urlparse(url).query)

    # `SignedHeaders` must not require `sentry-trace` or `baggage`.
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert _get_aws_sigv4_signed_headers_from_url_query_string(url) == {"host"}
    # presigned URL query must not contain `sentry-trace` or `baggage`.
    assert "sentry-trace" not in url
    assert "baggage" not in url


def _send(sentry_init, capture_items, service, messages, **options):
    """
    Send `messages`, i.e. `(body, message attributes)`, with the service's
    (batch) API; return the publish span and the sent messages.
    """
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()], **options)

    client = boto3.client(  # type: ignore[attr-defined]
        service,
        region_name="eu-north-1",
        aws_access_key_id="-",
        aws_secret_access_key="-",
    )

    # service -> (single method, batch method, destination, body key, entries key)
    single, batch, destination, body_key, entries_key = {
        "sqs": (
            "send_message",
            "send_message_batch",
            {"QueueUrl": "https://sqs.eu-north-1.amazonaws.com/123456789012/orders"},
            "MessageBody",
            "Entries",
        ),
        "sns": (
            "publish",
            "publish_batch",
            {"TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts"},
            "Message",
            "PublishBatchRequestEntries",
        ),
    }[service]

    entries = [
        {"Id": str(i), body_key: body, "MessageAttributes": attributes}
        for i, (body, attributes) in enumerate(messages)
    ]
    if len(entries) == 1:
        method, response = single, {"MessageId": "1"}
        (params,) = entries
        # single-method API uses `MessageId` instead of `Id`.
        del params["Id"]
        params.update(destination)
    else:
        method, response = batch, {"Successful": [], "Failed": []}
        params = {**destination, entries_key: entries}

    require_botocore_model_fields(client, method)

    sent_params = []
    client.meta.events.register(
        "before-parameter-build", lambda params, **kwargs: sent_params.append(params)
    )
    caller_params = copy.deepcopy(params)
    with Stubber(client) as stubber:
        stubber.add_response(method, response)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    # the caller's params are left as-is.
    assert params == caller_params
    (sent,) = sent_params
    (span,) = spans[OP.QUEUE_PUBLISH]
    return span, sent.get(entries_key, [sent])


@pytest.mark.parametrize("message_count", [1, 2], ids=["single", "batch"])
@pytest.mark.parametrize("service", ["sqs", "sns"])
def test_message_attributes_propagation(
    sentry_init, capture_items, service, message_count
):
    span, sent = _send(
        sentry_init,
        capture_items,
        service,
        [("hi", {"orderId": {"DataType": "String", "StringValue": "42"}})]
        * message_count,
    )

    # every batch entry carries the context of the publish span.
    assert len(sent) == message_count
    for message in sent:
        attributes = message["MessageAttributes"]
        assert attributes.keys() == {"orderId", "sentry-trace", "baggage"}
        assert attributes["sentry-trace"] == {
            "DataType": "String",
            "StringValue": f"{span['trace_id']}-{span['span_id']}-1",
        }
        assert attributes["baggage"]["DataType"] == "String"
        assert (
            f"sentry-trace_id={span['trace_id']}"
            in attributes["baggage"]["StringValue"]
        )


@pytest.mark.parametrize(
    "messages,options",
    [
        pytest.param(
            [("hi", {"baggage": {"DataType": "String", "StringValue": "vendor=1"}})],
            {},
            id="caller-carrier",
        ),
        pytest.param(
            [
                (
                    "hi",
                    {
                        f"attribute{i}": {"DataType": "String", "StringValue": "1"}
                        for i in range(9)
                    },
                )
            ],
            {},
            id="attribute-limit",
        ),
        pytest.param([("x" * 1024 * 256, {})], {}, id="message-size-limit"),
        # 2 * (131_000 + ~385) > 262_144 which exceeds the message size limit.
        pytest.param(
            [("x" * 131_000, {}), ("x" * 131_000, {})],
            {},
            id="batch-size-limit",
        ),
        pytest.param(
            [("hi", {})],
            {"trace_propagation_targets": ["example.com"]},
            id="trace-propagation-targets",
        ),
    ],
)
@pytest.mark.parametrize("service", ["sqs", "sns"])
def test_message_attributes_propagation_skipped(
    sentry_init, capture_items, service, messages, options
):
    _, sent = _send(sentry_init, capture_items, service, messages, **options)

    assert [message["MessageAttributes"] for message in sent] == [
        attributes for _, attributes in messages
    ]

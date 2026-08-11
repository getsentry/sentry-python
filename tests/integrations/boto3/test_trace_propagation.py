from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.config import Config

import sentry_sdk
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.utils import get_aws_sigv4_signed_headers


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


@pytest.mark.parametrize("span_streaming", [False, True])
def test_botocore_merges_propagation_before_sigv4_signing(sentry_init, span_streaming):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
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

        # register `before-sign` handler that adds third-party baggage.
        client.meta.events.register("before-sign", _inject_third_party_baggage)
        client.meta.events.register_last(
            "before-sign", capture_headers_after_instrumentation
        )

        if span_streaming:
            with sentry_sdk.traces.start_span(  # type: ignore[attr-defined]
                name="incoming"
            ):
                response = client.head_object(
                    Bucket="example-bucket",
                    Key="example-key",
                )
        else:
            with sentry_sdk.start_transaction(name="incoming", sampled=True):
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
        # preserves third-party baggage.
        assert "dd-origin=synthetics" in baggage
        assert "vendor=value" in baggage
        # add own `sentry-*` baggage.
        assert "sentry-trace_id=" in baggage
        assert "sentry-trace_id=stale" not in baggage
        # replace stale values instead of duplicating them.
        assert baggage.count("sentry-trace_id=") == 1
        assert baggage.count("sentry-sample_rand=") == 1

        # adds single `sentry-trace` header.
        sentry_trace_headers = headers.get_all("sentry-trace")
        assert sentry_trace_headers is not None
        assert len(sentry_trace_headers) == 1
        assert sentry_trace_headers == signed_request_headers["sentry-trace"]
        # both `baggage` and `sentry-trace` are signed.
        signed_headers = get_aws_sigv4_signed_headers(headers=headers)
        assert signed_headers >= {"baggage", "sentry-trace"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("span_streaming", [False, True])
def test_botocore_without_boto3_integration_preserves_signed_baggage(
    sentry_init, span_streaming
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
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

        # register `before-sign` handler that third-party signed baggage.
        client.meta.events.register("before-sign", _inject_signed_baggage)

        if span_streaming:
            with sentry_sdk.traces.start_span(  # type: ignore[attr-defined]
                name="incoming"
            ):
                response = client.head_object(
                    Bucket="example-bucket",
                    Key="example-key",
                )
        else:
            with sentry_sdk.start_transaction(name="incoming", sampled=True):
                response = client.head_object(
                    Bucket="example-bucket",
                    Key="example-key",
                )

        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
        headers = _AwsRequestHandler.requests[-1]
        # preserves third-party signed baggage.
        assert headers.get_all("baggage") == ["vendor=value"]
        # `httplib` still adds single `sentry-trace` header.
        assert len(headers.get_all("sentry-trace")) == 1
        signed_headers = get_aws_sigv4_signed_headers(headers=headers)
        assert "baggage" in signed_headers
        assert "sentry-trace" not in signed_headers
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_presigned_urls_do_not_require_sentry_headers(sentry_init):
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

    # only `host` header is signed.
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert get_aws_sigv4_signed_headers(headers={}, url=url) == {"host"}
    # no `sentry-*` or baggage are added.
    assert "sentry-trace" not in url
    assert "baggage" not in url

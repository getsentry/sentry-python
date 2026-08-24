from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest import mock

import pytest
from botocore.awsrequest import AWSHTTPConnection

import sentry_sdk
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.utils import _get_aws_sigv4_signed_headers


@pytest.fixture
def local_http_server():
    requests = []

    class TraceHeaderHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.headers)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), TraceHeaderHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield server, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _request(server, headers, path="/"):
    connection = AWSHTTPConnection("127.0.0.1", server.server_port)
    connection.request("POST", path, headers=dict(headers))

    response = connection.getresponse()
    response.read()
    connection.close()


@pytest.mark.parametrize("span_streaming", [False, True])
def test_aws_http_connection_appends_to_unsigned_baggage(
    sentry_init, local_http_server, span_streaming
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
        default_integrations=False,
        integrations=[StdlibIntegration()],
    )
    server, requests = local_http_server

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=67):
        if span_streaming:
            with sentry_sdk.traces.start_span(name="test"):  # type: ignore[attr-defined]
                _request(server, [("baggage", "vendor=value")])
        else:
            with sentry_sdk.start_transaction(name="test", sampled=True):
                _request(server, [("baggage", "vendor=value")])

    headers: HTTPMessage = requests[0]

    # preserve existing unsigned baggage
    baggage_headers = headers.get_all("baggage")
    assert baggage_headers is not None
    assert len(baggage_headers) == 2
    assert baggage_headers[0] == "vendor=value"
    assert baggage_headers[1].count("sentry-trace_id=") == 1
    assert "sentry-sample_rand=0.000067" in baggage_headers[1]
    sentry_trace_headers = headers.get_all("sentry-trace")
    assert sentry_trace_headers is not None
    assert len(sentry_trace_headers) == 1


@pytest.mark.parametrize("span_streaming", [False, True])
def test_aws_http_connection_skips_signed_baggage(
    sentry_init, local_http_server, span_streaming
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
        default_integrations=False,
        integrations=[StdlibIntegration()],
    )
    server, requests = local_http_server

    # simulate AWS SigV4 request that is already signed.
    authorization = (
        "AWS4-HMAC-SHA256 "
        "Credential=test/20260804/eu-west-1/secretsmanager/aws4_request, "
        "SignedHeaders=baggage;host;sentry-trace, "
        "Signature=sixtyseven"
    )

    if span_streaming:
        with sentry_sdk.traces.start_span(name="test"):  # type: ignore[attr-defined]
            _request(
                server,
                [
                    ("baggage", "vendor=value"),
                    ("sentry-trace", "existing-trace"),
                    ("Authorization", authorization),
                ],
            )
    else:
        with sentry_sdk.start_transaction(name="test", sampled=True):
            _request(
                server,
                [
                    ("baggage", "vendor=value"),
                    ("sentry-trace", "existing-trace"),
                    ("Authorization", authorization),
                ],
            )

    headers: HTTPMessage = requests[0]

    # do not append baggage after SigV4 signs it.
    assert headers.get_all("baggage") == ["vendor=value"]
    # preserves existing `sentry-trace` header.
    assert headers.get_all("sentry-trace") == ["existing-trace"]
    assert _get_aws_sigv4_signed_headers(
        authorization=headers.get("Authorization", "")
    ) >= {
        "baggage",
        "host",
        "sentry-trace",
    }


@pytest.mark.parametrize("span_streaming", [False, True])
def test_aws_http_connection_skips_query_signed_baggage(
    sentry_init, local_http_server, span_streaming
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
        default_integrations=False,
        integrations=[StdlibIntegration()],
    )
    server, requests = local_http_server
    path = (
        "/"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential="
        "test%2F20260804%2Feu-west-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20260804T120000Z"
        "&X-Amz-Expires=60"
        "&X-Amz-SignedHeaders=baggage%3Bhost"
        "&X-Amz-Signature=sixtyseven"
    )

    if span_streaming:
        with sentry_sdk.traces.start_span(name="test"):  # type: ignore[attr-defined]
            _request(
                server,
                [("baggage", "vendor=value")],
                path=path,
            )
    else:
        with sentry_sdk.start_transaction(name="test", sampled=True):
            _request(
                server,
                [("baggage", "vendor=value")],
                path=path,
            )

    headers: HTTPMessage = requests[0]
    # `baggage` is part of X-Amz-SignedHeaders, so may not be modified.
    baggage_headers = headers.get_all("baggage")
    assert baggage_headers is not None
    assert len(baggage_headers) == 1
    assert headers["baggage"] == "vendor=value"
    # `sentry-trace` was not signed, so it can be propagated.
    sentry_trace_headers = headers.get_all("sentry-trace")
    assert sentry_trace_headers is not None
    assert len(sentry_trace_headers) == 1
    assert _get_aws_sigv4_signed_headers(
        authorization=headers.get("Authorization", ""),
        url=f"http://127.0.0.1:{server.server_port}{path}",
    ) >= {"baggage", "host"}

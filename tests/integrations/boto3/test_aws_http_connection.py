from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from botocore.awsrequest import AWSHTTPConnection

import sentry_sdk
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.utils import (
    _get_aws_sigv4_signed_headers_from_authorization_header,
    _get_aws_sigv4_signed_headers_from_url_query_string,
)


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
def test_aws_http_connection_adds_missing_unsigned_propagation_headers(
    sentry_init, local_http_server, span_streaming
):
    """Add missing unsigned `sentry-trace` and `baggage`."""
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
        default_integrations=False,
        integrations=[StdlibIntegration()],
    )
    server, requests = local_http_server

    if span_streaming:
        with sentry_sdk.traces.start_span(name="test"):  # type: ignore[attr-defined]
            _request(server, [])
    else:
        with sentry_sdk.start_transaction(name="test", sampled=True):
            _request(server, [])

    headers: HTTPMessage = requests[0]

    # missing unsigned headers: add `sentry-trace` and `baggage`.
    baggage_headers = headers.get_all("baggage")
    assert baggage_headers is not None
    assert len(baggage_headers) == 1
    assert baggage_headers[0].count("sentry-trace_id=") == 1

    sentry_trace_headers = headers.get_all("sentry-trace")
    assert sentry_trace_headers is not None
    assert len(sentry_trace_headers) == 1


@pytest.mark.parametrize("span_streaming", [False, True])
def test_aws_http_connection_appends_baggage_but_preserves_sentry_trace(
    sentry_init, local_http_server, span_streaming
):
    """Append unsigned `baggage`; leave existing `sentry-trace` as-is."""
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
        default_integrations=False,
        integrations=[StdlibIntegration()],
    )
    server, requests = local_http_server

    if span_streaming:
        with sentry_sdk.traces.start_span(name="test"):  # type: ignore[attr-defined]
            _request(
                server,
                [
                    ("baggage", "vendor=value"),
                    ("sentry-trace", "existing-trace"),
                ],
            )
    else:
        with sentry_sdk.start_transaction(name="test", sampled=True):
            _request(
                server,
                [
                    ("baggage", "vendor=value"),
                    ("sentry-trace", "existing-trace"),
                ],
            )

    headers: HTTPMessage = requests[0]

    # unsigned `baggage`: append a second field.
    baggage_headers = headers.get_all("baggage")
    assert baggage_headers is not None
    assert len(baggage_headers) == 2
    assert baggage_headers[0] == "vendor=value"
    assert baggage_headers[1].count("sentry-trace_id=") == 1

    # existing `sentry-trace`: leave as-is since it is a single trace context.
    assert headers.get_all("sentry-trace") == ["existing-trace"]


@pytest.mark.parametrize("span_streaming", [False, True])
def test_aws_http_connection_preserves_signed_propagation_headers(
    sentry_init, local_http_server, span_streaming
):
    """Leave signed `sentry-trace` and `baggage` as-is."""
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
        default_integrations=False,
        integrations=[StdlibIntegration()],
    )
    server, requests = local_http_server

    # both propagation headers are named in `SignedHeaders`.
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

    # signed `baggage`: leave as-is.
    assert headers.get_all("baggage") == ["vendor=value"]
    # signed `sentry-trace`: leave as-is.
    assert headers.get_all("sentry-trace") == ["existing-trace"]
    assert _get_aws_sigv4_signed_headers_from_authorization_header(
        headers.get("Authorization", "")
    ) >= {
        "baggage",
        "host",
        "sentry-trace",
    }


@pytest.mark.parametrize("span_streaming", [False, True])
def test_aws_http_connection_preserves_query_signed_baggage(
    sentry_init, local_http_server, span_streaming
):
    """Leave query-signed `baggage` as-is; add unsigned `sentry-trace`."""
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
    # query-signed `baggage`: leave as-is.
    assert headers.get_all("baggage") == ["vendor=value"]
    # unsigned `sentry-trace`: add it.
    sentry_trace_headers = headers.get_all("sentry-trace")
    assert sentry_trace_headers is not None
    assert len(sentry_trace_headers) == 1
    assert _get_aws_sigv4_signed_headers_from_url_query_string(
        f"http://127.0.0.1:{server.server_port}{path}"
    ) >= {"baggage", "host"}

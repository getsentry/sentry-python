import datetime
import os
import socket
import time
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from socket import SocketIO
from threading import Thread
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

import sentry_sdk
from sentry_sdk import capture_message, continue_trace, start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration
from tests.conftest import ApproxDict, create_mock_http_server, get_free_port

PORT = create_mock_http_server()


class MockProxyRequestHandler(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        self.send_response(200, "Connection Established")
        self.end_headers()

        self.rfile.readline()

        response = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
        self.wfile.write(response)
        self.wfile.flush()


def create_mock_proxy_server():
    proxy_port = get_free_port()
    proxy_server = HTTPServer(("localhost", proxy_port), MockProxyRequestHandler)
    proxy_thread = Thread(target=proxy_server.serve_forever)
    proxy_thread.daemon = True
    proxy_thread.start()
    return proxy_port


PROXY_PORT = create_mock_proxy_server()

CHUNK_DELAY = 0.1
NUM_CHUNKS = 3


class ChunkedResponseHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for _ in range(NUM_CHUNKS):
            chunk = b"x" * 100
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
            time.sleep(CHUNK_DELAY)
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *args):
        pass


def create_chunked_server():
    port = get_free_port()
    server = HTTPServer(("localhost", port), ChunkedResponseHandler)
    thread = Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return port


CHUNKED_PORT = create_chunked_server()


def test_crumb_capture(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])
    events = capture_events()

    url = "http://localhost:{}/some/random/url".format(PORT)
    urlopen(url)

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


@pytest.mark.parametrize(
    "status_code,level",
    [
        (200, None),
        (301, None),
        (403, "warning"),
        (405, "warning"),
        (500, "error"),
    ],
)
def test_crumb_capture_client_error(sentry_init, capture_events, status_code, level):
    sentry_init(integrations=[StdlibIntegration()])
    events = capture_events()

    url = f"http://localhost:{PORT}/status/{status_code}"  # noqa:E231
    try:
        urlopen(url)
    except HTTPError:
        pass

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    if level is None:
        assert "level" not in crumb
    else:
        assert crumb["level"] == level

    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: status_code,
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


def test_crumb_capture_hint(sentry_init, capture_events):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[StdlibIntegration()], before_breadcrumb=before_breadcrumb)
    events = capture_events()

    url = "http://localhost:{}/some/random/url".format(PORT)
    urlopen(url)

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
            "extra": "foo",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_empty_realurl(
    sentry_init,
    span_streaming,
):
    """
    Ensure that after using sentry_sdk.init you can putrequest a
    None url.
    """

    sentry_init(
        dsn="",
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    HTTPConnection("localhost", port=PORT).putrequest("POST", None)


def test_httplib_misuse(sentry_init, capture_events, request):
    """HTTPConnection.getresponse must be called after every call to
    HTTPConnection.request. However, if somebody does not abide by
    this contract, we still should handle this gracefully and not
    send mixed breadcrumbs.

    Test whether our breadcrumbs are coherent when somebody uses HTTPConnection
    wrongly.
    """

    sentry_init()
    events = capture_events()

    conn = HTTPConnection("localhost", PORT)

    # make sure we release the resource, even if the test fails
    request.addfinalizer(conn.close)

    conn.request("GET", "/200")

    with pytest.raises(Exception):  # noqa: B017
        # This raises an exception, because we didn't call `getresponse` for
        # the previous request yet.
        #
        # This call should not affect our breadcrumb.
        conn.request("POST", "/200")

    response = conn.getresponse()
    assert response._method == "GET"

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "url": "http://localhost:{}/200".format(PORT),
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_outgoing_trace_headers(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    already_patched_getresponse = HTTPSConnection.getresponse
    request_headers = {}

    class HTTPSConnectionRecordingRequestHeaders(HTTPSConnection):
        def send(self, *args, **kwargs) -> None:
            request_str = args[0]
            for line in request_str.decode("utf-8").split("\r\n")[1:]:
                if line:
                    key, val = line.split(": ")
                    request_headers[key] = val

            server_sock, client_sock = socket.socketpair()
            server_sock.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            server_sock.close()
            self.sock = client_sock

        def getresponse(self, *args, **kwargs):
            return already_patched_getresponse(self, *args, **kwargs)

    events = capture_events()

    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
        "baggage": (
            "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
            "sentry-user_id=Am%C3%A9lie, sentry-sample_rand=0.132521102938283, other-vendor-value-2=foo;bar;"
        ),
    }

    if span_streaming:
        items = capture_items("span")
        sentry_sdk.traces.continue_trace(headers)

        with sentry_sdk.traces.start_span(
            name="/interactions/other-dogs/new-dog",
            attributes={
                "sentry.op": "greeting.sniff",
            },
        ):
            connection = HTTPSConnectionRecordingRequestHeaders("localhost", port=PORT)
            connection.request("GET", "/top-chasers")
            connection.getresponse()

        sentry_sdk.flush()
        request_span = next(item.payload for item in items if item.type == "span")
        expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=request_span["trace_id"],
            parent_span_id=request_span["span_id"],
            sampled=1,
        )
    else:
        events = capture_events()
        transaction = continue_trace(headers)

        with start_transaction(
            transaction=transaction,
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="12312012123120121231201212312012",
        ) as transaction:
            connection = HTTPSConnectionRecordingRequestHeaders("localhost", port=PORT)
            connection.request("GET", "/top-chasers")
            connection.getresponse()

        (event,) = events
        request_span = event["spans"][-1]
        expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=event["contexts"]["trace"]["trace_id"],
            parent_span_id=request_span["span_id"],
            sampled=1,
        )

    assert request_headers["sentry-trace"] == expected_sentry_trace

    expected_outgoing_baggage = (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
        "sentry-sample_rate=1.0,"
        "sentry-user_id=Am%C3%A9lie,"
        "sentry-sample_rand=0.132521102938283"
    )

    assert request_headers["baggage"] == expected_outgoing_baggage


@pytest.mark.parametrize("span_streaming", [True, False])
def test_outgoing_trace_headers_head_sdk(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=0.5,
        release="foo",
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    already_patched_getresponse = HTTPSConnection.getresponse
    request_headers = {}

    class HTTPSConnectionRecordingRequestHeaders(HTTPSConnection):
        def send(self, *args, **kwargs) -> None:
            request_str = args[0]
            for line in request_str.decode("utf-8").split("\r\n")[1:]:
                if line:
                    key, val = line.split(": ")
                    request_headers[key] = val

            server_sock, client_sock = socket.socketpair()
            server_sock.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            server_sock.close()
            self.sock = client_sock

        def getresponse(self, *args, **kwargs):
            return already_patched_getresponse(self, *args, **kwargs)

    if span_streaming:
        items = capture_items("span")

        sentry_sdk.traces.continue_trace({})

        with mock.patch(
            "sentry_sdk.tracing_utils.Random.randrange", return_value=250000
        ):
            with sentry_sdk.traces.start_span(name="Head SDK tx"):
                connection = HTTPSConnectionRecordingRequestHeaders(
                    "localhost", port=PORT
                )
                connection.request("GET", "/top-chasers")
                connection.getresponse()

        sentry_sdk.flush()
        request_span = next(item.payload for item in items if item.type == "span")
        expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=request_span["trace_id"],
            parent_span_id=request_span["span_id"],
            sampled=1,
        )

        expected_outgoing_baggage = (
            "sentry-trace_id=%s,"
            "sentry-sample_rand=0.250000,"
            "sentry-environment=production,"
            "sentry-release=foo,"
            "sentry-transaction=Head%%20SDK%%20tx,"
            "sentry-sample_rate=0.5,"
            "sentry-sampled=true"
        ) % request_span["trace_id"]
    else:
        events = capture_events()

        with mock.patch(
            "sentry_sdk.tracing_utils.Random.randrange", return_value=250000
        ):
            transaction = continue_trace({})

        with start_transaction(
            transaction=transaction, name="Head SDK tx"
        ) as transaction:
            connection = HTTPSConnectionRecordingRequestHeaders("localhost", port=PORT)
            connection.request("GET", "/top-chasers")
            connection.getresponse()

        (event,) = events
        request_span = event["spans"][-1]
        expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=event["contexts"]["trace"]["trace_id"],
            parent_span_id=request_span["span_id"],
            sampled=1,
        )

        expected_outgoing_baggage = (
            "sentry-trace_id=%s,"
            "sentry-sample_rand=0.250000,"
            "sentry-environment=production,"
            "sentry-release=foo,"
            "sentry-sample_rate=0.5,"
            "sentry-sampled=%s"
        ) % (transaction.trace_id, "true" if transaction.sampled else "false")

    assert request_headers["sentry-trace"] == expected_sentry_trace
    assert request_headers["baggage"] == expected_outgoing_baggage


@pytest.mark.parametrize(
    "trace_propagation_targets,host,path,trace_propagated",
    [
        [
            [],
            "example.com",
            "/",
            False,
        ],
        [
            None,
            "example.com",
            "/",
            False,
        ],
        [
            [MATCH_ALL],
            "example.com",
            "/",
            True,
        ],
        [
            ["https://example.com/"],
            "example.com",
            "/",
            True,
        ],
        [
            ["https://example.com/"],
            "example.com",
            "",
            False,
        ],
        [
            ["https://example.com"],
            "example.com",
            "",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "example.net",
            "",
            False,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "good.example.net",
            "",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "good.example.net",
            "/some/thing",
            True,
        ],
    ],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_option_trace_propagation_targets(
    sentry_init,
    trace_propagation_targets,
    host,
    path,
    trace_propagated,
    span_streaming,
):
    sentry_init(
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    already_patched_getresponse = HTTPSConnection.getresponse

    request_headers = {}

    class HTTPSConnectionRecordingRequestHeaders(HTTPSConnection):
        def send(self, *args, **kwargs) -> None:
            request_str = args[0]
            for line in request_str.decode("utf-8").split("\r\n")[1:]:
                if line:
                    key, val = line.split(": ")
                    request_headers[key] = val

            server_sock, client_sock = socket.socketpair()
            server_sock.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            server_sock.close()
            self.sock = client_sock

        def getresponse(self, *args, **kwargs):
            return already_patched_getresponse(self, *args, **kwargs)

    headers = {
        "baggage": (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, "
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
        )
    }

    if span_streaming:
        sentry_sdk.traces.continue_trace(headers)

        with sentry_sdk.traces.start_span(
            name="/interactions/other-dogs/new-dog",
            attributes={
                "sentry.op": "greeting.sniff",
            },
        ):
            connection = HTTPSConnectionRecordingRequestHeaders(host)
            connection.request("GET", path)
            connection.getresponse()
    else:
        transaction = continue_trace(headers)

        with start_transaction(
            transaction=transaction,
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="12312012123120121231201212312012",
        ) as transaction:
            connection = HTTPSConnectionRecordingRequestHeaders(host)
            connection.request("GET", path)
            connection.getresponse()

    if trace_propagated:
        assert "sentry-trace" in request_headers
        assert "baggage" in request_headers
    else:
        assert "sentry-trace" not in request_headers
        assert "baggage" not in request_headers


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_disabled(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_options = {
        "traces_sample_rate": 1.0,
        "enable_http_request_source": False,
        "http_request_source_threshold_ms": 0,
    }

    sentry_init(
        **sentry_options,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        sentry_sdk.flush()
        span = next(item.payload for item in items if item.type == "span")
        assert span["name"].startswith("GET")

        attributes = span["attributes"]

        assert SPANDATA.CODE_LINE_NUMBER not in attributes
        assert SPANDATA.CODE_NAMESPACE not in attributes
        assert SPANDATA.CODE_FILE_PATH not in attributes
        assert SPANDATA.CODE_FUNCTION not in attributes
    else:
        events = capture_events()

        with start_transaction(name="foo"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        (event,) = events

        span = event["spans"][-1]
        assert span["description"].startswith("GET")

        data = span.get("data", {})

        assert SPANDATA.CODE_LINENO not in data
        assert SPANDATA.CODE_NAMESPACE not in data
        assert SPANDATA.CODE_FILEPATH not in data
        assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize("enable_http_request_source", [None, True])
@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_enabled(
    sentry_init,
    capture_events,
    capture_items,
    enable_http_request_source,
    span_streaming,
):
    sentry_options = {
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
        "_experiments": {"trace_lifecycle": "stream" if span_streaming else "static"},
    }

    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    if span_streaming:
        sentry_init(
            **sentry_options,
        )

        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        sentry_sdk.flush()
        span = next(item.payload for item in items if item.type == "span")
        assert span["name"].startswith("GET")

        attributes = span["attributes"]

        assert SPANDATA.CODE_LINE_NUMBER in attributes
        assert SPANDATA.CODE_NAMESPACE in attributes
        assert SPANDATA.CODE_FILE_PATH in attributes
        assert SPANDATA.CODE_FUNCTION in attributes
    else:
        sentry_init(**sentry_options)

        events = capture_events()

        with start_transaction(name="foo"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        (event,) = events

        span = event["spans"][-1]
        assert span["description"].startswith("GET")

        data = span.get("data", {})

        assert SPANDATA.CODE_LINENO in data
        assert SPANDATA.CODE_NAMESPACE in data
        assert SPANDATA.CODE_FILEPATH in data
        assert SPANDATA.CODE_FUNCTION in data


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        sentry_sdk.flush()
        span = next(item.payload for item in items if item.type == "span")
        assert span["name"].startswith("GET")

        attributes = span["attributes"]

        assert SPANDATA.CODE_LINE_NUMBER in attributes
        assert SPANDATA.CODE_NAMESPACE in attributes
        assert SPANDATA.CODE_FILE_PATH in attributes
        assert SPANDATA.CODE_FUNCTION in attributes

        assert type(attributes.get(SPANDATA.CODE_LINE_NUMBER)) == int
        assert attributes.get(SPANDATA.CODE_LINE_NUMBER) > 0
        assert (
            attributes.get(SPANDATA.CODE_NAMESPACE)
            == "tests.integrations.stdlib.test_httplib"
        )
        assert attributes.get(SPANDATA.CODE_FILE_PATH).endswith(
            "tests/integrations/stdlib/test_httplib.py"
        )

        is_relative_path = attributes.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
        assert is_relative_path

        assert attributes.get(SPANDATA.CODE_FUNCTION) == "test_request_source"
    else:
        events = capture_events()

        with start_transaction(name="foo"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        (event,) = events

        span = event["spans"][-1]
        assert span["description"].startswith("GET")

        data = span.get("data", {})

        assert SPANDATA.CODE_LINENO in data
        assert SPANDATA.CODE_NAMESPACE in data
        assert SPANDATA.CODE_FILEPATH in data
        assert SPANDATA.CODE_FUNCTION in data

        assert type(data.get(SPANDATA.CODE_LINENO)) == int
        assert data.get(SPANDATA.CODE_LINENO) > 0
        assert (
            data.get(SPANDATA.CODE_NAMESPACE)
            == "tests.integrations.stdlib.test_httplib"
        )
        assert data.get(SPANDATA.CODE_FILEPATH).endswith(
            "tests/integrations/stdlib/test_httplib.py"
        )

        is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
        assert is_relative_path

        assert data.get(SPANDATA.CODE_FUNCTION) == "test_request_source"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_with_module_in_search_path(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            from httplib_helpers.helpers import get_request_with_connection

            conn = HTTPConnection("localhost", port=PORT)
            get_request_with_connection(conn, "/foo")

        sentry_sdk.flush()
        span = next(item.payload for item in items if item.type == "span")
        assert span["name"].startswith("GET")

        attributes = span["attributes"]

        assert SPANDATA.CODE_LINE_NUMBER in attributes
        assert SPANDATA.CODE_NAMESPACE in attributes
        assert SPANDATA.CODE_FILE_PATH in attributes
        assert SPANDATA.CODE_FUNCTION in attributes

        assert type(attributes.get(SPANDATA.CODE_LINE_NUMBER)) == int
        assert attributes.get(SPANDATA.CODE_LINE_NUMBER) > 0
        assert attributes.get(SPANDATA.CODE_NAMESPACE) == "httplib_helpers.helpers"
        assert attributes.get(SPANDATA.CODE_FILE_PATH) == "httplib_helpers/helpers.py"

        is_relative_path = attributes.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
        assert is_relative_path

        assert attributes.get(SPANDATA.CODE_FUNCTION) == "get_request_with_connection"
    else:
        events = capture_events()

        with start_transaction(name="foo"):
            from httplib_helpers.helpers import get_request_with_connection

            conn = HTTPConnection("localhost", port=PORT)
            get_request_with_connection(conn, "/foo")

        (event,) = events

        span = event["spans"][-1]
        assert span["description"].startswith("GET")

        data = span.get("data", {})

        assert SPANDATA.CODE_LINENO in data
        assert SPANDATA.CODE_NAMESPACE in data
        assert SPANDATA.CODE_FILEPATH in data
        assert SPANDATA.CODE_FUNCTION in data

        assert type(data.get(SPANDATA.CODE_LINENO)) == int
        assert data.get(SPANDATA.CODE_LINENO) > 0
        assert data.get(SPANDATA.CODE_NAMESPACE) == "httplib_helpers.helpers"
        assert data.get(SPANDATA.CODE_FILEPATH) == "httplib_helpers/helpers.py"

        is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
        assert is_relative_path

        assert data.get(SPANDATA.CODE_FUNCTION) == "get_request_with_connection"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_no_request_source_if_duration_too_short(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    add_http_request_source = sentry_sdk.tracing_utils.add_http_request_source

    def add_http_request_source_with_pinned_timestamps(span):
        if span_streaming:
            span._start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span._end_timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
            result = add_http_request_source(span)
            span._end_timestamp = None
            return result
        else:
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
            return add_http_request_source(span)

    if span_streaming:
        items = capture_items("span")
        with mock.patch(
            "sentry_sdk.integrations.stdlib.add_http_request_source",
            add_http_request_source_with_pinned_timestamps,
        ):
            with sentry_sdk.traces.start_span(name="foo"):
                conn = HTTPConnection("localhost", port=PORT)
                conn.request("GET", "/foo")
                conn.getresponse()

        sentry_sdk.flush()
        span = next(item.payload for item in items if item.type == "span")
        assert span["name"].startswith("GET")

        attributes = span["attributes"]

        assert SPANDATA.CODE_LINE_NUMBER not in attributes
        assert SPANDATA.CODE_NAMESPACE not in attributes
        assert SPANDATA.CODE_FILE_PATH not in attributes
        assert SPANDATA.CODE_FUNCTION not in attributes
    else:
        events = capture_events()

        with mock.patch(
            "sentry_sdk.integrations.stdlib.add_http_request_source",
            add_http_request_source_with_pinned_timestamps,
        ):
            with start_transaction(name="foo"):
                conn = HTTPConnection("localhost", port=PORT)
                conn.request("GET", "/foo")
                conn.getresponse()

        (event,) = events

        span = event["spans"][-1]
        assert span["description"].startswith("GET")

        data = span.get("data", {})

        assert SPANDATA.CODE_LINENO not in data
        assert SPANDATA.CODE_NAMESPACE not in data
        assert SPANDATA.CODE_FILEPATH not in data
        assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_if_duration_over_threshold(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    add_http_request_source = sentry_sdk.tracing_utils.add_http_request_source

    def add_http_request_source_with_pinned_timestamps(span):
        if span_streaming:
            span._start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span._end_timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)
            result = add_http_request_source(span)
            span._end_timestamp = None
            return result
        else:
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)
            return add_http_request_source(span)

    if span_streaming:
        items = capture_items("span")

        with mock.patch(
            "sentry_sdk.integrations.stdlib.add_http_request_source",
            add_http_request_source_with_pinned_timestamps,
        ):
            with sentry_sdk.traces.start_span(name="foo"):
                conn = HTTPConnection("localhost", port=PORT)
                conn.request("GET", "/foo")
                conn.getresponse()

        sentry_sdk.flush()
        span = next(item.payload for item in items if item.type == "span")
        assert span["name"].startswith("GET")

        attributes = span["attributes"]

        assert SPANDATA.CODE_LINE_NUMBER in attributes
        assert SPANDATA.CODE_NAMESPACE in attributes
        assert SPANDATA.CODE_FILE_PATH in attributes
        assert SPANDATA.CODE_FUNCTION in attributes

        assert type(attributes.get(SPANDATA.CODE_LINE_NUMBER)) == int
        assert attributes.get(SPANDATA.CODE_LINE_NUMBER) > 0
        assert (
            attributes.get(SPANDATA.CODE_NAMESPACE)
            == "tests.integrations.stdlib.test_httplib"
        )
        assert attributes.get(SPANDATA.CODE_FILE_PATH).endswith(
            "tests/integrations/stdlib/test_httplib.py"
        )

        is_relative_path = attributes.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
        assert is_relative_path

        assert (
            attributes.get(SPANDATA.CODE_FUNCTION)
            == "add_http_request_source_with_pinned_timestamps"
        )
    else:
        events = capture_events()

        with mock.patch(
            "sentry_sdk.integrations.stdlib.add_http_request_source",
            add_http_request_source_with_pinned_timestamps,
        ):
            with start_transaction(name="foo"):
                conn = HTTPConnection("localhost", port=PORT)
                conn.request("GET", "/foo")
                conn.getresponse()

        (event,) = events

        span = event["spans"][-1]
        assert span["description"].startswith("GET")

        data = span.get("data", {})

        assert SPANDATA.CODE_LINENO in data
        assert SPANDATA.CODE_NAMESPACE in data
        assert SPANDATA.CODE_FILEPATH in data
        assert SPANDATA.CODE_FUNCTION in data

        assert type(data.get(SPANDATA.CODE_LINENO)) == int
        assert data.get(SPANDATA.CODE_LINENO) > 0
        assert (
            data.get(SPANDATA.CODE_NAMESPACE)
            == "tests.integrations.stdlib.test_httplib"
        )
        assert data.get(SPANDATA.CODE_FILEPATH).endswith(
            "tests/integrations/stdlib/test_httplib.py"
        )

        is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
        assert is_relative_path

        assert (
            data.get(SPANDATA.CODE_FUNCTION)
            == "add_http_request_source_with_pinned_timestamps"
        )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        debug=True,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert spans[1]["attributes"]["sentry.origin"] == "manual"

        assert spans[0]["attributes"]["sentry.op"] == "http.client"
        assert spans[0]["attributes"]["sentry.origin"] == "auto.http.stdlib.httplib"
    else:
        events = capture_events()

        with start_transaction(name="foo"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/foo")
            conn.getresponse()

        (event,) = events
        assert event["contexts"]["trace"]["origin"] == "manual"

        assert event["spans"][0]["op"] == "http.client"
        assert event["spans"][0]["origin"] == "auto.http.stdlib.httplib"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_http_timeout(
    monkeypatch,
    sentry_init,
    capture_envelopes,
    capture_items,
    span_streaming,
):
    mock_readinto = mock.Mock(side_effect=TimeoutError)
    monkeypatch.setattr(SocketIO, "readinto", mock_readinto)

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("span")

        with pytest.raises(TimeoutError):
            with sentry_sdk.traces.start_span(
                name="name",
                attributes={
                    "sentry.op": "op",
                },
            ):
                conn = HTTPConnection("localhost", port=PORT)
                conn.request("GET", "/bla")
                conn.getresponse()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 2
        span = spans[0]
        assert span["attributes"]["sentry.op"] == "http.client"
        assert span["name"] == f"GET http://localhost:{PORT}/bla"  # noqa: E231
    else:
        envelopes = capture_envelopes()

        with pytest.raises(TimeoutError):
            with start_transaction(op="op", name="name"):
                conn = HTTPConnection("localhost", port=PORT)
                conn.request("GET", "/bla")
                conn.getresponse()

        (transaction_envelope,) = envelopes
        transaction = transaction_envelope.get_transaction_event()
        assert len(transaction["spans"]) == 1

        span = transaction["spans"][0]
        assert span["op"] == "http.client"
        assert span["description"] == f"GET http://localhost:{PORT}/bla"  # noqa: E231


@pytest.mark.parametrize("tunnel_port", [8080, None])
@pytest.mark.parametrize("span_streaming", [True, False])
def test_proxy_http_tunnel(
    sentry_init,
    capture_events,
    capture_items,
    tunnel_port,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            conn = HTTPConnection("localhost", PROXY_PORT)
            conn.set_tunnel("api.example.com", tunnel_port)
            conn.request("GET", "/foo")
            conn.getresponse()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        (span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.origin") == "auto.http.stdlib.httplib"
        )

        port_modifier = f":{tunnel_port}" if tunnel_port else ""
        assert span["name"] == f"GET http://api.example.com{port_modifier}/foo"
        assert (
            span["attributes"][SPANDATA.URL_FULL]
            == f"http://api.example.com{port_modifier}/foo"
        )
        assert span["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "GET"
        assert span["attributes"][SPANDATA.NETWORK_PEER_ADDRESS] == "localhost"
        assert span["attributes"][SPANDATA.NETWORK_PEER_PORT] == PROXY_PORT
    else:
        events = capture_events()

        with start_transaction(name="test_transaction"):
            conn = HTTPConnection("localhost", PROXY_PORT)
            conn.set_tunnel("api.example.com", tunnel_port)
            conn.request("GET", "/foo")
            conn.getresponse()

        (event,) = events
        (span,) = event["spans"]

        port_modifier = f":{tunnel_port}" if tunnel_port else ""
        assert span["description"] == f"GET http://api.example.com{port_modifier}/foo"
        assert span["data"]["url"] == f"http://api.example.com{port_modifier}/foo"
        assert span["data"][SPANDATA.HTTP_METHOD] == "GET"
        assert span["data"][SPANDATA.NETWORK_PEER_ADDRESS] == "localhost"
        assert span["data"][SPANDATA.NETWORK_PEER_PORT] == PROXY_PORT


@pytest.mark.parametrize("span_streaming", [True, False])
def test_chunked_response_span_covers_body_read(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    min_expected_duration = CHUNK_DELAY * NUM_CHUNKS

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            conn = HTTPConnection("localhost", CHUNKED_PORT)
            conn.request("GET", "/chunked")
            response = conn.getresponse()
            response.read()

        sentry_sdk.flush()
        http_span, parent_span = [item.payload for item in items]

        duration = http_span["end_timestamp"] - http_span["start_timestamp"]
        assert duration >= min_expected_duration
    else:
        events = capture_events()

        with start_transaction(name="test_chunked"):
            conn = HTTPConnection("localhost", CHUNKED_PORT)
            conn.request("GET", "/chunked")
            response = conn.getresponse()
            response.read()

        (event,) = events
        (span,) = event["spans"]

        fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
        start = datetime.datetime.strptime(span["start_timestamp"], fmt)
        end = datetime.datetime.strptime(span["timestamp"], fmt)
        duration = (end - start).total_seconds()
        assert duration >= min_expected_duration

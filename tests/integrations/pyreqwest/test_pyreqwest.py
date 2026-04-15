import datetime
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest import mock
import pytest

from pyreqwest.client import ClientBuilder, SyncClientBuilder
from pyreqwest.simple.request import pyreqwest_get as async_pyreqwest_get
from pyreqwest.simple.sync_request import pyreqwest_get as sync_pyreqwest_get

import sentry_sdk
from sentry_sdk import start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.pyreqwest import PyreqwestIntegration
from tests.conftest import get_free_port


class PyreqwestMockHandler(BaseHTTPRequestHandler):
    captured_requests = []

    def do_GET(self) -> None:
        self.captured_requests.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        )

        code = 200
        if "/status/" in self.path:
            try:
                code = int(self.path.split("/")[-1])
            except (ValueError, IndexError):
                code = 200

        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def server_port():
    port = get_free_port()
    server = HTTPServer(("localhost", port), PyreqwestMockHandler)
    thread = Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield port
    server.shutdown()


@pytest.fixture(autouse=True)
def clear_captured_requests():
    PyreqwestMockHandler.captured_requests.clear()


def test_sync_client_spans(sentry_init, capture_events, server_port):
    sentry_init(integrations=[PyreqwestIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    url = f"http://localhost:{server_port}/hello?q=test#frag"
    with start_transaction(name="test_transaction"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

    (event,) = events
    assert len(event["spans"]) == 1
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET http://localhost:{server_port}/hello"
    assert span["data"]["url"] == f"http://localhost:{server_port}/hello"
    assert span["data"][SPANDATA.HTTP_METHOD] == "GET"
    assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["data"][SPANDATA.HTTP_QUERY] == "q=test"
    assert span["data"][SPANDATA.HTTP_FRAGMENT] == "frag"
    assert span["origin"] == "auto.http.pyreqwest"


@pytest.mark.asyncio
async def test_async_client_spans(sentry_init, capture_events, server_port):
    sentry_init(integrations=[PyreqwestIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    url = f"http://localhost:{server_port}/hello"
    async with ClientBuilder().build() as client:
        with start_transaction(name="test_transaction"):
            response = await client.get(url).build().send()
            assert response.status == 200

    (event,) = events
    assert len(event["spans"]) == 1
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET {url}"
    assert span["data"]["url"] == url
    assert span["data"][SPANDATA.HTTP_METHOD] == "GET"
    assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["origin"] == "auto.http.pyreqwest"


def test_sync_simple_request_spans(sentry_init, capture_events, server_port):
    sentry_init(integrations=[PyreqwestIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    url = f"http://localhost:{server_port}/hello-simple"
    with start_transaction(name="test_transaction"):
        response = sync_pyreqwest_get(url).send()
        assert response.status == 200

    (event,) = events
    assert len(event["spans"]) == 1
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET {url}"
    assert span["data"]["url"] == url
    assert span["data"][SPANDATA.HTTP_METHOD] == "GET"
    assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["origin"] == "auto.http.pyreqwest"


@pytest.mark.asyncio
async def test_async_simple_request_spans(sentry_init, capture_events, server_port):
    sentry_init(integrations=[PyreqwestIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    url = f"http://localhost:{server_port}/hello-simple-async"
    with start_transaction(name="test_transaction"):
        response = await async_pyreqwest_get(url).send()
        assert response.status == 200

    (event,) = events
    assert len(event["spans"]) == 1
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET {url}"
    assert span["data"]["url"] == url
    assert span["data"][SPANDATA.HTTP_METHOD] == "GET"
    assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["origin"] == "auto.http.pyreqwest"


def test_span_origin(sentry_init, capture_events, server_port):
    sentry_init(integrations=[PyreqwestIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    url = f"http://localhost:{server_port}/origin"
    with start_transaction(name="test_transaction"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    (event,) = events
    assert event["spans"][0]["origin"] == "auto.http.pyreqwest"


def test_outgoing_trace_headers(sentry_init, server_port):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_propagation_targets=["localhost"],
    )

    url = f"http://localhost:{server_port}/trace"
    with start_transaction(
        name="test_transaction", trace_id="01234567890123456789012345678901"
    ):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

    assert len(PyreqwestMockHandler.captured_requests) == 1
    headers = PyreqwestMockHandler.captured_requests[0]["headers"]

    assert "sentry-trace" in headers
    assert headers["sentry-trace"].startswith("01234567890123456789012345678901")
    assert "baggage" in headers
    assert "sentry-trace_id=01234567890123456789012345678901" in headers["baggage"]


def test_outgoing_trace_headers_append_to_baggage(sentry_init, server_port):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_propagation_targets=["localhost"],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    url = f"http://localhost:{server_port}/baggage"

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with start_transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="01234567890123456789012345678901",
        ):
            client = SyncClientBuilder().build()
            client.get(url).header("baggage", "custom=data").build().send()

    assert len(PyreqwestMockHandler.captured_requests) == 1
    headers = PyreqwestMockHandler.captured_requests[0]["headers"]

    assert "baggage" in headers
    baggage = headers["baggage"]
    assert "custom=data" in baggage
    assert "sentry-trace_id=01234567890123456789012345678901" in baggage
    assert "sentry-sample_rand=0.500000" in baggage
    assert "sentry-environment=production" in baggage
    assert "sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42" in baggage
    assert "sentry-transaction=/interactions/other-dogs/new-dog" in baggage
    assert "sentry-sample_rate=1.0" in baggage
    assert "sentry-sampled=true" in baggage


@pytest.mark.parametrize(
    "trace_propagation_targets,trace_propagated",
    [
        [None, False],
        [[], False],
        [[MATCH_ALL], True],
        [["localhost"], True],
        [[r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"], False],
    ],
)
def test_trace_propagation_targets(
    sentry_init, server_port, trace_propagation_targets, trace_propagated
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
    )

    url = f"http://localhost:{server_port}/propagation"

    with start_transaction():
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    assert len(PyreqwestMockHandler.captured_requests) == 1
    headers = PyreqwestMockHandler.captured_requests[0]["headers"]

    if trace_propagated:
        assert "sentry-trace" in headers
    else:
        assert "sentry-trace" not in headers


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(sentry_init, capture_events, server_port):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    url = f"http://localhost:{server_port}/parse-fail"

    with start_transaction(name="test_transaction"):
        with mock.patch(
            "sentry_sdk.integrations.pyreqwest.parse_url",
            side_effect=ValueError,
        ):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

    (event,) = events
    span = event["spans"][0]

    assert span["description"] == "GET [Filtered]"
    assert span["data"][SPANDATA.HTTP_METHOD] == "GET"
    assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert "url" not in span["data"]
    assert SPANDATA.HTTP_QUERY not in span["data"]
    assert SPANDATA.HTTP_FRAGMENT not in span["data"]


def test_request_source_disabled(sentry_init, capture_events, server_port):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
    )
    events = capture_events()

    url = f"http://localhost:{server_port}/hello"

    with start_transaction(name="test_transaction"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    (event,) = events
    span = event["spans"][0]
    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize("enable_http_request_source", [None, True])
def test_request_source_enabled(
    sentry_init, capture_events, server_port, enable_http_request_source
):
    sentry_options = {
        "integrations": [PyreqwestIntegration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)
    events = capture_events()

    url = f"http://localhost:{server_port}/hello"

    with start_transaction(name="test_transaction"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    (event,) = events
    span = event["spans"][0]
    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data


def test_request_source(sentry_init, capture_events, server_port):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )
    events = capture_events()

    url = f"http://localhost:{server_port}/hello"

    with start_transaction(name="test_transaction"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    (event,) = events
    span = event["spans"][0]
    data = span.get("data", {})

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert (
        data.get(SPANDATA.CODE_NAMESPACE)
        == "tests.integrations.pyreqwest.test_pyreqwest"
    )
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/pyreqwest/test_pyreqwest.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_request_source"


def test_request_source_with_module_in_search_path(
    sentry_init, capture_events, server_port
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )
    events = capture_events()

    url = f"http://localhost:{server_port}/hello"

    with start_transaction(name="test_transaction"):
        from pyreqwest_helpers.helpers import get_request_with_client

        client = SyncClientBuilder().build()
        get_request_with_client(client, url)

    (event,) = events
    span = event["spans"][0]
    data = span.get("data", {})

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert data.get(SPANDATA.CODE_NAMESPACE) == "pyreqwest_helpers.helpers"
    assert data.get(SPANDATA.CODE_FILEPATH) == "pyreqwest_helpers/helpers.py"

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "get_request_with_client"


def test_no_request_source_if_duration_too_short(
    sentry_init, capture_events, server_port
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
    )
    events = capture_events()

    url = f"http://localhost:{server_port}/hello"

    with start_transaction(name="test_transaction"):

        @contextmanager
        def fake_start_span(*args, **kwargs):
            with sentry_sdk.start_span(*args, **kwargs) as span:
                pass
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
            yield span

        with mock.patch(
            "sentry_sdk.integrations.pyreqwest.start_span",
            fake_start_span,
        ):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

    (event,) = events
    span = event["spans"][-1]
    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


def test_request_source_if_duration_over_threshold(
    sentry_init, capture_events, server_port
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
    )
    events = capture_events()

    url = f"http://localhost:{server_port}/hello"

    with start_transaction(name="test_transaction"):

        @contextmanager
        def fake_start_span(*args, **kwargs):
            with sentry_sdk.start_span(*args, **kwargs) as span:
                pass
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)
            yield span

        with mock.patch(
            "sentry_sdk.integrations.pyreqwest.start_span",
            fake_start_span,
        ):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

    (event,) = events
    span = event["spans"][-1]
    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

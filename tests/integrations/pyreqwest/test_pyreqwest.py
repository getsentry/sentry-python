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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_sync_client_spans(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello?q=test#frag"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            client = SyncClientBuilder().build()
            response = client.get(url).build().send()
            assert response.status == 200

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 2
        span = spans[0]
        assert span["attributes"]["sentry.op"] == "http.client"
        assert span["name"] == f"GET http://localhost:{server_port}/hello"
        assert span["attributes"]["url"] == f"http://localhost:{server_port}/hello"
        assert span["attributes"][SPANDATA.HTTP_METHOD] == "GET"
        assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
        assert span["attributes"][SPANDATA.HTTP_QUERY] == "q=test"
        assert span["attributes"][SPANDATA.HTTP_FRAGMENT] == "frag"
        assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"
    else:
        events = capture_events()

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_async_client_spans(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello"
    if span_streaming:
        items = capture_items("span")

        async with ClientBuilder().build() as client:
            with sentry_sdk.traces.start_span(name="custom parent"):
                response = await client.get(url).build().send()
                assert response.status == 200

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 2
        span = spans[0]
        assert span["attributes"]["sentry.op"] == "http.client"
        assert span["name"] == f"GET {url}"
        assert span["attributes"]["url"] == url
        assert span["attributes"][SPANDATA.HTTP_METHOD] == "GET"
        assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
        assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"
    else:
        events = capture_events()

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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_sync_simple_request_spans(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello-simple"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            response = sync_pyreqwest_get(url).send()
            assert response.status == 200

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 2
        span = spans[0]
        assert span["attributes"]["sentry.op"] == "http.client"
        assert span["name"] == f"GET {url}"
        assert span["attributes"]["url"] == url
        assert span["attributes"][SPANDATA.HTTP_METHOD] == "GET"
        assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
        assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"
    else:
        events = capture_events()

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_async_simple_request_spans(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello-simple-async"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            response = await async_pyreqwest_get(url).send()
            assert response.status == 200

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert len(spans) == 2
        span = spans[0]
        assert span["attributes"]["sentry.op"] == "http.client"
        assert span["name"] == f"GET {url}"
        assert span["attributes"]["url"] == url
        assert span["attributes"][SPANDATA.HTTP_METHOD] == "GET"
        assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
        assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"
    else:
        events = capture_events()

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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/origin"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["attributes"]["sentry.origin"] == "auto.http.pyreqwest"
    else:
        events = capture_events()

        with start_transaction(name="test_transaction"):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

        (event,) = events
        assert event["spans"][0]["origin"] == "auto.http.pyreqwest"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_outgoing_trace_headers(
    sentry_init,
    server_port,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_propagation_targets=["localhost"],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/trace"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(
            name="custom parent",
        ):
            client = SyncClientBuilder().build()
            response = client.get(url).build().send()
            assert response.status == 200

        assert len(PyreqwestMockHandler.captured_requests) == 1
        headers = PyreqwestMockHandler.captured_requests[0]["headers"]

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        http_span = next(
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "http.client"
        )

        assert "sentry-trace" in headers
        assert headers["sentry-trace"].startswith(http_span["trace_id"])
        assert "baggage" in headers
        assert f"sentry-trace_id={http_span['trace_id']}" in headers["baggage"]
    else:
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_outgoing_trace_headers_append_to_baggage(
    sentry_init,
    server_port,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_propagation_targets=["localhost"],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/baggage"
    if span_streaming:
        items = capture_items("span")

        with mock.patch(
            "sentry_sdk.tracing_utils.Random.randrange", return_value=500000
        ):
            with sentry_sdk.traces.start_span(
                name="/interactions/other-dogs/new-dog",
                attributes={
                    "sentry.op": "greeting.sniff",
                },
            ):
                client = SyncClientBuilder().build()
                client.get(url).header("baggage", "custom=data").build().send()

        assert len(PyreqwestMockHandler.captured_requests) == 1
        headers = PyreqwestMockHandler.captured_requests[0]["headers"]

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        http_span = next(
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "http.client"
        )

        assert "baggage" in headers
        baggage = headers["baggage"]
        assert "custom=data" in baggage
        assert f"sentry-trace_id={http_span['trace_id']}" in baggage
    else:
        with mock.patch(
            "sentry_sdk.tracing_utils.Random.randrange", return_value=500000
        ):
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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_trace_propagation_targets(
    sentry_init,
    server_port,
    trace_propagation_targets,
    trace_propagated,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_omit_url_data_if_parsing_fails(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/parse-fail"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            with mock.patch(
                "sentry_sdk.integrations.pyreqwest.parse_url",
                side_effect=ValueError,
            ):
                client = SyncClientBuilder().build()
                client.get(url).build().send()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]

        assert span["name"] == "GET [Filtered]"
        assert span["attributes"][SPANDATA.HTTP_METHOD] == "GET"
        assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
        assert "url" not in span["attributes"]
        assert SPANDATA.HTTP_QUERY not in span["attributes"]
        assert SPANDATA.HTTP_FRAGMENT not in span["attributes"]
    else:
        events = capture_events()

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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_disabled(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]
        data = span.get("data", {})

        assert SPANDATA.CODE_LINE_NUMBER not in data
        assert SPANDATA.CODE_NAMESPACE not in data
        assert SPANDATA.CODE_FILE_PATH not in data
    else:
        events = capture_events()

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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_enabled(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    enable_http_request_source,
    span_streaming,
):
    sentry_options = {
        "integrations": [PyreqwestIntegration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
        "_experiments": {"trace_lifecycle": "stream" if span_streaming else "static"},
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    url = f"http://localhost:{server_port}/hello"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]
        data = span.get("attributes", {})

        assert SPANDATA.CODE_LINE_NUMBER in data
        assert SPANDATA.CODE_NAMESPACE in data
        assert SPANDATA.CODE_FILE_PATH in data
    else:
        events = capture_events()

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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]
        data = span.get("attributes", {})

        assert type(data.get(SPANDATA.CODE_LINE_NUMBER)) == int
        assert data.get(SPANDATA.CODE_LINE_NUMBER) > 0
        assert (
            data.get(SPANDATA.CODE_NAMESPACE)
            == "tests.integrations.pyreqwest.test_pyreqwest"
        )
        assert data.get(SPANDATA.CODE_FILE_PATH).endswith(
            "tests/integrations/pyreqwest/test_pyreqwest.py"
        )

        is_relative_path = data.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
    else:
        events = capture_events()

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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_with_module_in_search_path(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    url = f"http://localhost:{server_port}/hello"

    if span_streaming:
        with sentry_sdk.traces.start_span(name="custom parent"):
            from pyreqwest_helpers.helpers import get_request_with_client

            client = SyncClientBuilder().build()
            get_request_with_client(client, url)

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]
        data = span.get("attributes", {})

        assert type(data.get(SPANDATA.CODE_LINE_NUMBER)) == int
        assert data.get(SPANDATA.CODE_LINE_NUMBER) > 0
        assert data.get(SPANDATA.CODE_NAMESPACE) == "pyreqwest_helpers.helpers"
        assert data.get(SPANDATA.CODE_FILE_PATH) == "pyreqwest_helpers/helpers.py"

        is_relative_path = data.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
    else:
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_no_request_source_if_duration_too_short(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            original_start_span = sentry_sdk.traces.start_span

            @contextmanager
            def fake_start_span(*args, **kwargs):
                with original_start_span(*args, **kwargs) as span:
                    span._start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
                    span._end_timestamp = datetime.datetime(
                        2024, 1, 1, microsecond=100001
                    )

                    yield span

                    span._end_timestamp = None

            with mock.patch(
                "sentry_sdk.integrations.pyreqwest.sentry_sdk.traces.start_span",
                fake_start_span,
            ):
                client = SyncClientBuilder().build()
                client.get(url).build().send()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]
        data = span.get("data", {})

        assert SPANDATA.CODE_LINE_NUMBER not in data
        assert SPANDATA.CODE_NAMESPACE not in data
        assert SPANDATA.CODE_FILE_PATH not in data
    else:
        events = capture_events()

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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_source_if_duration_over_threshold(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
    span_streaming,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    url = f"http://localhost:{server_port}/hello"
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            original_start_span = sentry_sdk.traces.start_span

            @contextmanager
            def fake_start_span(*args, **kwargs):
                with original_start_span(*args, **kwargs) as span:
                    span._start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
                    span._end_timestamp = datetime.datetime(
                        2024, 1, 1, microsecond=100001
                    )

                    yield span

                    span._end_timestamp = None

            with mock.patch(
                "sentry_sdk.integrations.pyreqwest.sentry_sdk.traces.start_span",
                fake_start_span,
            ):
                client = SyncClientBuilder().build()
                client.get(url).build().send()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]
        span = spans[0]
        data = span.get("attributes", {})

        assert SPANDATA.CODE_LINE_NUMBER in data
        assert SPANDATA.CODE_NAMESPACE in data
        assert SPANDATA.CODE_FILE_PATH in data
    else:
        events = capture_events()

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

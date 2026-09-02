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
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.pyreqwest import PyreqwestIntegration
from tests.conftest import ApproxDict, get_free_port


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


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_sync_client_spans(
    sentry_init,
    capture_items,
    server_port,
    send_default_pii,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello?q=test#frag"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2
    span = spans[0]
    assert span["attributes"]["sentry.op"] == "http.client"
    assert span["name"] == f"GET http://localhost:{server_port}/hello"
    assert span["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "GET"
    assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"

    if send_default_pii:
        assert (
            span["attributes"]["url.full"]
            == f"http://localhost:{server_port}/hello?q=test#frag"
        )
        assert span["attributes"][SPANDATA.URL_QUERY] == "q=test"
        assert span["attributes"][SPANDATA.URL_FRAGMENT] == "frag"
    else:
        assert "url.full" not in span["attributes"]
        assert SPANDATA.URL_QUERY not in span["attributes"]
        assert SPANDATA.URL_FRAGMENT not in span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_async_client_spans(
    sentry_init,
    capture_items,
    server_port,
    send_default_pii,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello"
    items = capture_items("span")

    async with ClientBuilder().build() as client:
        with sentry_sdk.traces.start_span(name="custom parent"):
            response = await client.get(url).build().send()
            assert response.status == 200

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2
    span = spans[0]
    assert span["attributes"]["sentry.op"] == "http.client"
    assert span["name"] == f"GET {url}"
    assert span["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "GET"
    assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"

    if send_default_pii:
        assert span["attributes"]["url.full"] == url
    else:
        assert "url.full" not in span["attributes"]


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_sync_simple_request_spans(
    sentry_init,
    capture_items,
    server_port,
    send_default_pii,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello-simple"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        response = sync_pyreqwest_get(url).send()
        assert response.status == 200

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2
    span = spans[0]
    assert span["attributes"]["sentry.op"] == "http.client"
    assert span["name"] == f"GET {url}"
    assert span["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "GET"
    assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"

    if send_default_pii:
        assert span["attributes"]["url.full"] == url
    else:
        assert "url.full" not in span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_async_simple_request_spans(
    sentry_init,
    capture_items,
    server_port,
    send_default_pii,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello-simple-async"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        response = await async_pyreqwest_get(url).send()
        assert response.status == 200

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2
    span = spans[0]
    assert span["attributes"]["sentry.op"] == "http.client"
    assert span["name"] == f"GET {url}"
    assert span["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "GET"
    assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert span["attributes"]["sentry.origin"] == "auto.http.pyreqwest"

    if send_default_pii:
        assert span["attributes"]["url.full"] == url
    else:
        assert "url.full" not in span["attributes"]


def test_span_origin(
    sentry_init,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/origin"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert spans[0]["attributes"]["sentry.origin"] == "auto.http.pyreqwest"


def test_outgoing_trace_headers(
    sentry_init,
    server_port,
    capture_items,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_propagation_targets=["localhost"],
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/trace"
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
    spans = [item.payload for item in items]
    http_span = next(
        span for span in spans if span["attributes"].get("sentry.op") == "http.client"
    )

    assert "sentry-trace" in headers
    assert headers["sentry-trace"].startswith(http_span["trace_id"])
    assert "baggage" in headers
    assert f"sentry-trace_id={http_span['trace_id']}" in headers["baggage"]


def test_outgoing_trace_headers_append_to_baggage(
    sentry_init,
    server_port,
    capture_items,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_propagation_targets=["localhost"],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/baggage"
    items = capture_items("span")

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
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
    spans = [item.payload for item in items]
    http_span = next(
        span for span in spans if span["attributes"].get("sentry.op") == "http.client"
    )

    assert "baggage" in headers
    baggage = headers["baggage"]
    assert "custom=data" in baggage
    assert f"sentry-trace_id={http_span['trace_id']}" in baggage
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
    sentry_init,
    server_port,
    trace_propagation_targets,
    trace_propagated,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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
def test_omit_url_data_if_parsing_fails(
    sentry_init,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/parse-fail"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        with mock.patch(
            "sentry_sdk.integrations.pyreqwest.parse_url",
            side_effect=ValueError,
        ):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]

    assert span["name"] == "GET [Filtered]"
    assert span["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "GET"
    assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 200
    assert "url.full" not in span["attributes"]
    assert SPANDATA.URL_QUERY not in span["attributes"]
    assert SPANDATA.URL_FRAGMENT not in span["attributes"]


def test_request_source_disabled(
    sentry_init,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]
    data = span.get("attributes", {})

    assert SPANDATA.CODE_LINE_NUMBER not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILE_PATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize("enable_http_request_source", [None, True])
def test_request_source_enabled(
    sentry_init,
    capture_items,
    server_port,
    enable_http_request_source,
):
    sentry_options = {
        "integrations": [PyreqwestIntegration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
        "trace_lifecycle": "stream",
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    url = f"http://localhost:{server_port}/hello"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]
    data = span.get("attributes", {})

    assert SPANDATA.CODE_LINE_NUMBER in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILE_PATH in data
    assert SPANDATA.CODE_FUNCTION in data


def test_request_source(
    sentry_init,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        client.get(url).build().send()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
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
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_request_source"


def test_request_source_with_module_in_search_path(
    sentry_init,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        from pyreqwest_helpers.helpers import get_request_with_client

        client = SyncClientBuilder().build()
        get_request_with_client(client, url)

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]
    data = span.get("attributes", {})

    assert type(data.get(SPANDATA.CODE_LINE_NUMBER)) == int
    assert data.get(SPANDATA.CODE_LINE_NUMBER) > 0
    assert data.get(SPANDATA.CODE_NAMESPACE) == "pyreqwest_helpers.helpers"
    assert data.get(SPANDATA.CODE_FILE_PATH) == "pyreqwest_helpers/helpers.py"

    is_relative_path = data.get(SPANDATA.CODE_FILE_PATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "get_request_with_client"


def test_no_request_source_if_duration_too_short(
    sentry_init,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        original_start_span = sentry_sdk.traces.start_span

        @contextmanager
        def fake_start_span(*args, **kwargs):
            with original_start_span(*args, **kwargs) as span:
                span._start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
                span._end_timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)

                yield span

                span._end_timestamp = None

        with mock.patch(
            "sentry_sdk.integrations.pyreqwest.sentry_sdk.traces.start_span",
            fake_start_span,
        ):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]
    data = span.get("attributes", {})

    assert SPANDATA.CODE_LINE_NUMBER not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILE_PATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


def test_request_source_if_duration_over_threshold(
    sentry_init,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello"
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        original_start_span = sentry_sdk.traces.start_span

        @contextmanager
        def fake_start_span(*args, **kwargs):
            with original_start_span(*args, **kwargs) as span:
                span._start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
                span._end_timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)

                yield span

                span._end_timestamp = None

        with mock.patch(
            "sentry_sdk.integrations.pyreqwest.sentry_sdk.traces.start_span",
            fake_start_span,
        ):
            client = SyncClientBuilder().build()
            client.get(url).build().send()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    span = spans[0]
    data = span.get("attributes", {})

    assert SPANDATA.CODE_LINE_NUMBER in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILE_PATH in data
    assert SPANDATA.CODE_FUNCTION in data


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_crumb_capture(
    sentry_init,
    capture_events,
    server_port,
    send_default_pii,
):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(
        integrations=[PyreqwestIntegration()],
        before_breadcrumb=before_breadcrumb,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello?q=test#frag"

    events = capture_events()

    client = SyncClientBuilder().build()
    response = client.get(url).build().send()
    assert response.status == 200

    capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    expected = {
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: 200,
        "extra": "foo",
    }
    if send_default_pii:
        expected["url"] = f"http://localhost:{server_port}/hello?q=test#frag"
        expected[SPANDATA.HTTP_QUERY] = "q=test"
        expected[SPANDATA.HTTP_FRAGMENT] = "frag"

    assert crumb["data"] == ApproxDict(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_async_crumb_capture(
    sentry_init,
    capture_events,
    server_port,
    send_default_pii,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        send_default_pii=send_default_pii,
    )

    url = f"http://localhost:{server_port}/hello?q=test#frag"

    events = capture_events()

    # Ensure the isolation scope contextvar is set before pyreqwest spawns
    # its middleware on a separate asyncio Task. Without this, the child task
    # lazily creates its own isolation scope, and breadcrumbs added there
    # don't propagate back to this task's context.
    sentry_sdk.get_isolation_scope()

    with sentry_sdk.start_transaction():
        async with ClientBuilder().build() as client:
            response = await client.get(url).build().send()
            assert response.status == 200

        capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    expected = {
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: 200,
    }
    if send_default_pii:
        expected["url"] = f"http://localhost:{server_port}/hello"
        expected[SPANDATA.HTTP_QUERY] = "q=test"
        expected[SPANDATA.HTTP_FRAGMENT] = "frag"

    assert crumb["data"] == ApproxDict(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_async_crumb_capture_span_streaming(
    sentry_init,
    capture_events,
    server_port,
    send_default_pii,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    url = f"http://localhost:{server_port}/hello?q=test#frag"

    events = capture_events()

    with sentry_sdk.traces.start_span(name="segment"):
        async with ClientBuilder().build() as client:
            response = await client.get(url).build().send()
            assert response.status == 200

            capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    expected = {
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: 200,
    }
    if send_default_pii:
        expected["url"] = f"http://localhost:{server_port}/hello?q=test#frag"
        expected[SPANDATA.HTTP_QUERY] = "q=test"
        expected[SPANDATA.HTTP_FRAGMENT] = "frag"

    assert crumb["data"] == ApproxDict(expected)


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
def test_crumb_capture_client_error(
    sentry_init,
    capture_events,
    server_port,
    status_code,
    level,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
    )

    url = f"http://localhost:{server_port}/status/{status_code}"

    events = capture_events()

    with sentry_sdk.start_transaction():
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == status_code

        capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    if level is None:
        assert "level" not in crumb
    else:
        assert crumb["level"] == level

    assert crumb["data"] == ApproxDict(
        {
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: status_code,
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
def test_crumb_capture_client_error_span_streaming(
    sentry_init,
    capture_events,
    server_port,
    status_code,
    level,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
    )

    url = f"http://localhost:{server_port}/status/{status_code}"

    events = capture_events()

    with sentry_sdk.traces.start_span(name="segment"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == status_code

        capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    if level is None:
        assert "level" not in crumb
    else:
        assert crumb["level"] == level

    assert crumb["data"] == ApproxDict(
        {
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: status_code,
        }
    )


@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        pytest.param(
            {"send_default_pii": True},
            "toy=tennisball&color=red&auth=secret",
            id="send_default_pii_true",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            id="send_default_pii_false",
        ),
        pytest.param(
            {},
            None,
            id="defaults",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "denylist", "terms": ["toy"]}
                    }
                }
            },
            "toy=%5BFiltered%5D&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_custom_terms",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["auth"]}
                    }
                }
            },
            "toy=%5BFiltered%5D&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist_sensitive_term",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            None,
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            None,
            id="data_collection_wins_over_send_default_pii",
        ),
    ],
)
def test_url_query_data_collection_span_streaming_sync(
    sentry_init,
    capture_items,
    server_port,
    init_kwargs,
    expected_query,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = f"http://localhost:{server_port}/hello?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

    sentry_sdk.flush()

    span = [item.payload for item in items][0]

    if expected_query is None:
        assert SPANDATA.URL_QUERY not in span["attributes"]
    else:
        assert span["attributes"][SPANDATA.URL_QUERY] == expected_query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        pytest.param(
            {"send_default_pii": True},
            "toy=tennisball&color=red&auth=secret",
            id="send_default_pii_true",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            id="send_default_pii_false",
        ),
        pytest.param(
            {},
            None,
            id="defaults",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "denylist", "terms": ["toy"]}
                    }
                }
            },
            "toy=%5BFiltered%5D&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_custom_terms",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["auth"]}
                    }
                }
            },
            "toy=%5BFiltered%5D&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist_sensitive_term",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            None,
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            None,
            id="data_collection_wins_over_send_default_pii",
        ),
    ],
)
async def test_url_query_data_collection_span_streaming_async(
    sentry_init,
    capture_items,
    server_port,
    init_kwargs,
    expected_query,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = f"http://localhost:{server_port}/hello?toy=tennisball&color=red&auth=secret#frag"

    async with ClientBuilder().build() as client:
        with sentry_sdk.traces.start_span(name="custom parent"):
            response = await client.get(url).build().send()
            assert response.status == 200

    sentry_sdk.flush()

    span = [item.payload for item in items][0]

    if expected_query is None:
        assert SPANDATA.URL_QUERY not in span["attributes"]
    else:
        assert span["attributes"][SPANDATA.URL_QUERY] == expected_query


@pytest.mark.parametrize(
    "init_kwargs, expected_suffix",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "?toy=tennisball&color=red&auth=%5BFiltered%5D#frag",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "?toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D#frag",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {"send_default_pii": True},
            "?toy=tennisball&color=red&auth=secret#frag",
            id="send_default_pii_true",
        ),
    ],
)
def test_url_full_reassembly_span_streaming_sync(
    sentry_init,
    capture_items,
    server_port,
    init_kwargs,
    expected_suffix,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

    sentry_sdk.flush()

    span = [item.payload for item in items][0]

    assert span["attributes"][SPANDATA.URL_FULL] == base_url + expected_suffix


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_suffix",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "?toy=tennisball&color=red&auth=%5BFiltered%5D#frag",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "?toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D#frag",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {"send_default_pii": True},
            "?toy=tennisball&color=red&auth=secret#frag",
            id="send_default_pii_true",
        ),
    ],
)
async def test_url_full_reassembly_span_streaming_async(
    sentry_init,
    capture_items,
    server_port,
    init_kwargs,
    expected_suffix,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    async with ClientBuilder().build() as client:
        with sentry_sdk.traces.start_span(name="custom parent"):
            response = await client.get(url).build().send()
            assert response.status == 200

    sentry_sdk.flush()

    span = [item.payload for item in items][0]

    assert span["attributes"][SPANDATA.URL_FULL] == base_url + expected_suffix


@pytest.mark.parametrize(
    "init_kwargs, expected_suffix",
    [
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "#frag",
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            "#frag",
            id="data_collection_wins_over_send_default_pii",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            id="send_default_pii_false",
        ),
    ],
)
def test_url_query_params_off_keeps_bare_url_span_streaming_sync(
    sentry_init,
    capture_items,
    server_port,
    init_kwargs,
    expected_suffix,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="custom parent"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

    sentry_sdk.flush()

    span = [item.payload for item in items][0]

    assert SPANDATA.URL_QUERY not in span["attributes"]

    if expected_suffix is None:
        assert SPANDATA.URL_FULL not in span["attributes"]
        assert SPANDATA.URL_FRAGMENT not in span["attributes"]
    else:
        assert span["attributes"][SPANDATA.URL_FULL] == base_url + expected_suffix
        assert span["attributes"][SPANDATA.URL_FRAGMENT] == "frag"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_suffix",
    [
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "#frag",
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            "#frag",
            id="data_collection_wins_over_send_default_pii",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            id="send_default_pii_false",
        ),
    ],
)
async def test_url_query_params_off_keeps_bare_url_span_streaming_async(
    sentry_init,
    capture_items,
    server_port,
    init_kwargs,
    expected_suffix,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    async with ClientBuilder().build() as client:
        with sentry_sdk.traces.start_span(name="custom parent"):
            response = await client.get(url).build().send()
            assert response.status == 200

    sentry_sdk.flush()

    span = [item.payload for item in items][0]

    assert SPANDATA.URL_QUERY not in span["attributes"]

    if expected_suffix is None:
        assert SPANDATA.URL_FULL not in span["attributes"]
        assert SPANDATA.URL_FRAGMENT not in span["attributes"]
    else:
        assert span["attributes"][SPANDATA.URL_FULL] == base_url + expected_suffix
        assert span["attributes"][SPANDATA.URL_FRAGMENT] == "frag"


@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "",
            id="data_collection_off",
        ),
    ],
)
def test_crumb_url_query_data_collection_sync(
    sentry_init,
    capture_events,
    server_port,
    init_kwargs,
    expected_query,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        trace_lifecycle="stream",
        **init_kwargs,
    )

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    events = capture_events()

    with sentry_sdk.traces.start_span(name="segment"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

        capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]

    expected_url = base_url
    if expected_query:
        expected_url += "?" + expected_query
    expected_url += "#frag"

    assert crumb["data"]["url"] == expected_url
    assert crumb["data"][SPANDATA.HTTP_QUERY] == expected_query
    assert crumb["data"][SPANDATA.HTTP_FRAGMENT] == "frag"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "",
            id="data_collection_off",
        ),
    ],
)
async def test_crumb_url_query_data_collection_async(
    sentry_init,
    capture_events,
    server_port,
    init_kwargs,
    expected_query,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        trace_lifecycle="stream",
        **init_kwargs,
    )

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    events = capture_events()

    with sentry_sdk.traces.start_span(name="segment"):
        async with ClientBuilder().build() as client:
            response = await client.get(url).build().send()
            assert response.status == 200

            capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]

    expected_url = base_url
    if expected_query:
        expected_url += "?" + expected_query
    expected_url += "#frag"

    assert crumb["data"]["url"] == expected_url
    assert crumb["data"][SPANDATA.HTTP_QUERY] == expected_query
    assert crumb["data"][SPANDATA.HTTP_FRAGMENT] == "frag"


@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "",
            id="data_collection_off",
        ),
    ],
)
def test_crumb_url_query_data_collection_legacy_sync(
    sentry_init,
    capture_events,
    server_port,
    init_kwargs,
    expected_query,
):
    """
    Legacy (non span streaming) breadcrumbs report the bare URL, but the query
    is still filtered according to the data collection configuration. Remove
    when we've dropped transaction support and have fully migrated to span
    streaming.
    """
    sentry_init(integrations=[PyreqwestIntegration()], **init_kwargs)

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    events = capture_events()

    client = SyncClientBuilder().build()
    response = client.get(url).build().send()
    assert response.status == 200

    capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]

    assert crumb["data"]["url"] == base_url
    assert crumb["data"][SPANDATA.HTTP_QUERY] == expected_query
    assert crumb["data"][SPANDATA.HTTP_FRAGMENT] == "frag"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "",
            id="data_collection_off",
        ),
    ],
)
async def test_crumb_url_query_data_collection_legacy_async(
    sentry_init,
    capture_events,
    server_port,
    init_kwargs,
    expected_query,
):
    """
    Legacy (non span streaming) breadcrumbs report the bare URL, but the query
    is still filtered according to the data collection configuration. Remove
    when we've dropped transaction support and have fully migrated to span
    streaming.
    """
    sentry_init(integrations=[PyreqwestIntegration()], **init_kwargs)

    base_url = f"http://localhost:{server_port}/hello"
    url = f"{base_url}?toy=tennisball&color=red&auth=secret#frag"

    events = capture_events()

    sentry_sdk.get_isolation_scope()

    with sentry_sdk.start_transaction():
        async with ClientBuilder().build() as client:
            response = await client.get(url).build().send()
            assert response.status == 200

        capture_message("Testing!")

    event = next(e for e in events if e.get("breadcrumbs"))

    crumb = event["breadcrumbs"]["values"][0]

    assert crumb["data"]["url"] == base_url
    assert crumb["data"][SPANDATA.HTTP_QUERY] == expected_query
    assert crumb["data"][SPANDATA.HTTP_FRAGMENT] == "frag"


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails_span_streaming(
    sentry_init,
    capture_events,
    capture_items,
    server_port,
):
    sentry_init(
        integrations=[PyreqwestIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        _experiments={"data_collection": {}},
    )

    items = capture_items("span")

    url = f"http://localhost:{server_port}/hello?toy=tennisball&color=red&auth=secret#frag"

    events = capture_events()

    with sentry_sdk.traces.start_span(name="segment"):
        with mock.patch(
            "sentry_sdk.integrations.pyreqwest.parse_url",
            side_effect=ValueError,
        ):
            client = SyncClientBuilder().build()
            response = client.get(url).build().send()
            assert response.status == 200

        capture_message("Testing!")

    (event,) = events

    sentry_sdk.flush()

    span = [item.payload for item in items][0]

    assert SPANDATA.URL_FULL not in span["attributes"]
    assert SPANDATA.URL_QUERY not in span["attributes"]
    assert SPANDATA.URL_FRAGMENT not in span["attributes"]

    crumb = event["breadcrumbs"]["values"][0]

    assert "url" not in crumb["data"]
    assert SPANDATA.HTTP_QUERY not in crumb["data"]
    assert SPANDATA.HTTP_FRAGMENT not in crumb["data"]

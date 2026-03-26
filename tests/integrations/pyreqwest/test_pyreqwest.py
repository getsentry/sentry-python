from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import pytest

from pyreqwest.client import ClientBuilder, SyncClientBuilder
from pyreqwest.simple.request import pyreqwest_get as async_pyreqwest_get
from pyreqwest.simple.sync_request import pyreqwest_get as sync_pyreqwest_get

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
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

    url = f"http://localhost:{server_port}/hello"
    with start_transaction(name="test_transaction"):
        client = SyncClientBuilder().build()
        response = client.get(url).build().send()
        assert response.status == 200

    (event,) = events
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET {url}"
    assert span["data"]["url"] == url
    assert span["data"][SPANDATA.HTTP_STATUS_CODE] == 200
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
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET {url}"
    assert span["data"]["url"] == url
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
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET {url}"


@pytest.mark.asyncio
async def test_async_simple_request_spans(sentry_init, capture_events, server_port):
    sentry_init(integrations=[PyreqwestIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    url = f"http://localhost:{server_port}/hello-simple-async"
    with start_transaction(name="test_transaction"):
        response = await async_pyreqwest_get(url).send()
        assert response.status == 200

    (event,) = events
    span = event["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET {url}"


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

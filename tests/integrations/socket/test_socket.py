import socket

import sentry_sdk
from sentry_sdk.integrations.socket import SocketIntegration
from tests.conftest import create_mock_http_server

PORT = create_mock_http_server()


def test_getaddrinfo_trace(sentry_init, capture_items):
    sentry_init(
        integrations=[SocketIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="root"):
        socket.getaddrinfo("localhost", PORT)

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    dns_span, _root = spans

    assert dns_span["attributes"]["sentry.op"] == "socket.dns"
    assert dns_span["attributes"]["sentry.origin"] == "auto.socket.socket"
    assert dns_span["name"] == f"localhost:{PORT}"  # noqa: E231
    assert dns_span["attributes"]["server.address"] == "localhost"
    assert dns_span["attributes"]["server.port"] == PORT


def test_create_connection_trace(sentry_init, capture_items):
    timeout = 10

    sentry_init(
        integrations=[SocketIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="root"):
        socket.create_connection(("localhost", PORT), timeout, None)

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    # as getaddrinfo gets called in create_connection it should also contain a dns span
    # spans finish in order: dns (inner) ends first, connect ends, then root
    dns_span, connect_span, _root = spans

    assert connect_span["attributes"]["sentry.op"] == "socket.connection"
    assert connect_span["name"] == f"localhost:{PORT}"  # noqa: E231
    assert connect_span["attributes"]["server.address"] == "localhost"
    assert connect_span["attributes"]["server.port"] == PORT

    assert dns_span["attributes"]["sentry.op"] == "socket.dns"
    assert dns_span["name"] == f"localhost:{PORT}"  # noqa: E231
    assert dns_span["attributes"]["server.address"] == "localhost"
    assert dns_span["attributes"]["server.port"] == PORT


def test_span_origin(sentry_init, capture_items):
    sentry_init(
        integrations=[SocketIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="foo"):
        socket.create_connection(("localhost", PORT), 1, None)

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    dns_span, connect_span, _root = spans

    assert connect_span["attributes"]["sentry.op"] == "socket.connection"
    assert connect_span["attributes"]["sentry.origin"] == "auto.socket.socket"

    assert dns_span["attributes"]["sentry.op"] == "socket.dns"
    assert dns_span["attributes"]["sentry.origin"] == "auto.socket.socket"

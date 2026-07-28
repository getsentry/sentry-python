import socket

import pytest

import sentry_sdk
from sentry_sdk import start_transaction
from sentry_sdk.integrations.socket import SocketIntegration
from tests.conftest import ApproxDict, create_mock_http_server

PORT = create_mock_http_server()


@pytest.mark.parametrize("span_streaming", [True, False])
def test_getaddrinfo_trace(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[SocketIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
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
    else:
        events = capture_events()

        with start_transaction():
            socket.getaddrinfo("localhost", PORT)

        (event,) = events
        (span,) = event["spans"]

        assert span["op"] == "socket.dns"
        assert span["description"] == f"localhost:{PORT}"  # noqa: E231
        assert span["data"] == ApproxDict(
            {
                "host": "localhost",
                "port": PORT,
            }
        )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_create_connection_trace(
    sentry_init, capture_events, capture_items, span_streaming
):
    timeout = 10

    sentry_init(
        integrations=[SocketIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
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
    else:
        events = capture_events()

        with start_transaction():
            socket.create_connection(("localhost", PORT), timeout, None)

        (event,) = events
        (connect_span, dns_span) = event["spans"]
        # as getaddrinfo gets called in create_connection it should also contain a dns span

        assert connect_span["op"] == "socket.connection"
        assert connect_span["description"] == f"localhost:{PORT}"  # noqa: E231
        assert connect_span["data"] == ApproxDict(
            {
                "address": ["localhost", PORT],
                "timeout": timeout,
                "source_address": None,
            }
        )

        assert dns_span["op"] == "socket.dns"
        assert dns_span["description"] == f"localhost:{PORT}"  # noqa: E231
        assert dns_span["data"] == ApproxDict(
            {
                "host": "localhost",
                "port": PORT,
            }
        )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[SocketIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    if span_streaming:
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
    else:
        events = capture_events()

        with start_transaction(name="foo"):
            socket.create_connection(("localhost", PORT), 1, None)

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"

        assert event["spans"][0]["op"] == "socket.connection"
        assert event["spans"][0]["origin"] == "auto.socket.socket"

        assert event["spans"][1]["op"] == "socket.dns"
        assert event["spans"][1]["origin"] == "auto.socket.socket"

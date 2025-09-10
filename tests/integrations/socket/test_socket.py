import socket

from sentry_sdk import start_transaction
from sentry_sdk.integrations.socket import SocketIntegration
from tests.conftest import ApproxDict, create_mock_http_server

PORT = create_mock_http_server()


def test_getaddrinfo_trace(sentry_init, capture_events):
    sentry_init(integrations=[SocketIntegration()], traces_sample_rate=1.0)
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


def test_create_connection_trace(sentry_init, capture_events):
    timeout = 10

    sentry_init(integrations=[SocketIntegration()], traces_sample_rate=1.0)
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


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[SocketIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with start_transaction(name="foo"):
        socket.create_connection(("localhost", PORT), 1, None)

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"

    assert event["spans"][0]["op"] == "socket.connection"
    assert event["spans"][0]["origin"] == "auto.socket.socket"

    assert event["spans"][1]["op"] == "socket.dns"
    assert event["spans"][1]["origin"] == "auto.socket.socket"

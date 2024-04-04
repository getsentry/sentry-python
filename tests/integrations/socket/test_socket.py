import socket

from sentry_sdk import start_transaction
from sentry_sdk.integrations.socket import SocketIntegration
from tests.conftest import ApproxDict


def test_getaddrinfo_trace(sentry_init, capture_events):
    sentry_init(integrations=[SocketIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction():
        socket.getaddrinfo("example.com", 443)

    (event,) = events
    (span,) = event["spans"]

    assert span["op"] == "socket.dns"
    assert span["description"] == "example.com:443"
    assert span["data"] == ApproxDict(
        {
            "host": "example.com",
            "port": 443,
        }
    )


def test_create_connection_trace(sentry_init, capture_events):
    timeout = 10

    sentry_init(integrations=[SocketIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction():
        socket.create_connection(("example.com", 443), timeout, None)

    (event,) = events
    (connect_span, dns_span) = event["spans"]
    # as getaddrinfo gets called in create_connection it should also contain a dns span

    assert connect_span["op"] == "socket.connection"
    assert connect_span["description"] == "example.com:443"
    assert connect_span["data"] == ApproxDict(
        {
            "address": ["example.com", 443],
            "timeout": timeout,
            "source_address": None,
        }
    )

    assert dns_span["op"] == "socket.dns"
    assert dns_span["description"] == "example.com:443"
    assert dns_span["data"] == ApproxDict(
        {
            "host": "example.com",
            "port": 443,
        }
    )

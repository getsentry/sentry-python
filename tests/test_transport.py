import logging
import pickle
import os
import socket
import sys
import asyncio
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from tests.conftest import CapturingServer

try:
    import httpcore
except (ImportError, ModuleNotFoundError):
    httpcore = None

try:
    import gevent  # noqa: F401

    running_under_gevent = True
except ImportError:
    running_under_gevent = False

skip_under_gevent = pytest.mark.skipif(
    running_under_gevent, reason="Async tests not compatible with gevent"
)

import sentry_sdk
from sentry_sdk import (
    Client,
    add_breadcrumb,
    capture_message,
    isolation_scope,
    get_isolation_scope,
    Hub,
)
from sentry_sdk._compat import PY37, PY38
from sentry_sdk.envelope import Envelope, Item, parse_json, PayloadRef
from sentry_sdk.transport import (
    KEEP_ALIVE_SOCKET_OPTIONS,
    _parse_rate_limits,
    AsyncHttpTransport,
    HttpTransport,
)
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration


server = None


@pytest.fixture(scope="module", autouse=True)
def make_capturing_server(request):
    global server
    server = CapturingServer()
    server.start()
    request.addfinalizer(server.stop)


@pytest.fixture
def capturing_server():
    global server
    server.clear_captured()
    return server


@pytest.fixture
def make_client(request, capturing_server):
    def inner(**kwargs):
        return Client(
            "http://foobar@{}/132".format(capturing_server.url[len("http://") :]),
            **kwargs,
        )

    return inner


def mock_transaction_envelope(span_count: int) -> "Envelope":
    event = defaultdict(
        mock.MagicMock,
        type="transaction",
        spans=[mock.MagicMock() for _ in range(span_count)],
    )

    envelope = Envelope()
    envelope.add_transaction(event)

    return envelope


@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("client_flush_method", ["close", "flush"])
@pytest.mark.parametrize("use_pickle", (True, False))
@pytest.mark.parametrize("compression_level", (0, 9, None))
@pytest.mark.parametrize(
    "compression_algo",
    (("gzip", "br", "<invalid>", None) if PY37 else ("gzip", "<invalid>", None)),
)
@pytest.mark.parametrize("http2", [True, False] if PY38 else [False])
def test_transport_works(
    capturing_server,
    request,
    capsys,
    caplog,
    debug,
    make_client,
    client_flush_method,
    use_pickle,
    compression_level,
    compression_algo,
    http2,
):
    caplog.set_level(logging.DEBUG)

    experiments = {}
    if compression_level is not None:
        experiments["transport_compression_level"] = compression_level

    if compression_algo is not None:
        experiments["transport_compression_algo"] = compression_algo

    if http2:
        experiments["transport_http2"] = True

    client = make_client(
        debug=debug,
        _experiments=experiments,
    )

    if use_pickle:
        client = pickle.loads(pickle.dumps(client))

    sentry_sdk.get_global_scope().set_client(client)
    request.addfinalizer(lambda: sentry_sdk.get_global_scope().set_client(None))

    add_breadcrumb(
        level="info", message="i like bread", timestamp=datetime.now(timezone.utc)
    )
    capture_message("löl")

    getattr(client, client_flush_method)()

    out, err = capsys.readouterr()
    assert not err and not out
    assert capturing_server.captured
    should_compress = (
        # default is to compress with brotli if available, gzip otherwise
        (compression_level is None)
        or (
            # setting compression level to 0 means don't compress
            compression_level > 0
        )
    ) and (
        # if we couldn't resolve to a known algo, we don't compress
        compression_algo != "<invalid>"
    )

    assert capturing_server.captured[0].compressed == should_compress

    assert any("Sending envelope" in record.msg for record in caplog.records) == debug


@pytest.mark.parametrize(
    "num_pools,expected_num_pools",
    (
        (None, 2),
        (2, 2),
        (10, 10),
    ),
)
def test_transport_num_pools(make_client, num_pools, expected_num_pools):
    _experiments = {}
    if num_pools is not None:
        _experiments["transport_num_pools"] = num_pools

    client = make_client(_experiments=_experiments)

    options = client.transport._get_pool_options()
    assert options["num_pools"] == expected_num_pools


@pytest.mark.parametrize(
    "http2", [True, False] if sys.version_info >= (3, 8) else [False]
)
def test_two_way_ssl_authentication(make_client, http2):
    _experiments = {}
    if http2:
        _experiments["transport_http2"] = True

    current_dir = os.path.dirname(__file__)
    cert_file = f"{current_dir}/test.pem"
    key_file = f"{current_dir}/test.key"
    client = make_client(
        cert_file=cert_file,
        key_file=key_file,
        _experiments=_experiments,
    )
    options = client.transport._get_pool_options()

    if http2:
        assert options["ssl_context"] is not None
    else:
        assert options["cert_file"] == cert_file
        assert options["key_file"] == key_file


def test_socket_options(make_client):
    socket_options = [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.SOL_TCP, socket.TCP_KEEPINTVL, 10),
        (socket.SOL_TCP, socket.TCP_KEEPCNT, 6),
    ]

    client = make_client(socket_options=socket_options)

    options = client.transport._get_pool_options()
    assert options["socket_options"] == socket_options


def test_keep_alive_true(make_client):
    client = make_client(keep_alive=True)

    options = client.transport._get_pool_options()
    assert options["socket_options"] == KEEP_ALIVE_SOCKET_OPTIONS


def test_keep_alive_on_by_default(make_client):
    client = make_client()
    options = client.transport._get_pool_options()
    assert "socket_options" not in options


def test_default_timeout(make_client):
    client = make_client()

    options = client.transport._get_pool_options()
    assert "timeout" in options
    assert options["timeout"].total == client.transport.TIMEOUT


@pytest.mark.skipif(not PY38, reason="HTTP2 libraries are only available in py3.8+")
def test_default_timeout_http2(make_client):
    client = make_client(_experiments={"transport_http2": True})

    with mock.patch(
        "sentry_sdk.transport.httpcore.ConnectionPool.request",
        return_value=httpcore.Response(200),
    ) as request_mock:
        sentry_sdk.get_global_scope().set_client(client)
        capture_message("hi")
        client.flush()

    request_mock.assert_called_once()
    assert request_mock.call_args.kwargs["extensions"] == {
        "timeout": {
            "pool": client.transport.TIMEOUT,
            "connect": client.transport.TIMEOUT,
            "write": client.transport.TIMEOUT,
            "read": client.transport.TIMEOUT,
        }
    }


@pytest.mark.skipif(not PY38, reason="HTTP2 libraries are only available in py3.8+")
def test_http2_with_https_dsn(make_client):
    client = make_client(_experiments={"transport_http2": True})
    client.transport.parsed_dsn.scheme = "https"
    options = client.transport._get_pool_options()
    assert options["http2"] is True


@pytest.mark.skipif(not PY38, reason="HTTP2 libraries are only available in py3.8+")
def test_no_http2_with_http_dsn(make_client):
    client = make_client(_experiments={"transport_http2": True})
    client.transport.parsed_dsn.scheme = "http"
    options = client.transport._get_pool_options()
    assert options["http2"] is False


def test_socket_options_override_keep_alive(make_client):
    socket_options = [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.SOL_TCP, socket.TCP_KEEPINTVL, 10),
        (socket.SOL_TCP, socket.TCP_KEEPCNT, 6),
    ]

    client = make_client(socket_options=socket_options, keep_alive=False)

    options = client.transport._get_pool_options()
    assert options["socket_options"] == socket_options


def test_socket_options_merge_with_keep_alive(make_client):
    socket_options = [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 42),
        (socket.SOL_TCP, socket.TCP_KEEPINTVL, 42),
    ]

    client = make_client(socket_options=socket_options, keep_alive=True)

    options = client.transport._get_pool_options()
    try:
        assert options["socket_options"] == [
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 42),
            (socket.SOL_TCP, socket.TCP_KEEPINTVL, 42),
            (socket.SOL_TCP, socket.TCP_KEEPIDLE, 45),
            (socket.SOL_TCP, socket.TCP_KEEPCNT, 6),
        ]
    except AttributeError:
        assert options["socket_options"] == [
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 42),
            (socket.SOL_TCP, socket.TCP_KEEPINTVL, 42),
            (socket.SOL_TCP, socket.TCP_KEEPCNT, 6),
        ]


def test_socket_options_override_defaults(make_client):
    # If socket_options are set to [], this doesn't mean the user doesn't want
    # any custom socket_options, but rather that they want to disable the urllib3
    # socket option defaults, so we need to set this and not ignore it.
    client = make_client(socket_options=[])

    options = client.transport._get_pool_options()
    assert options["socket_options"] == []


def test_transport_infinite_loop(capturing_server, request, make_client):
    client = make_client(
        debug=True,
        # Make sure we cannot create events from our own logging
        integrations=[LoggingIntegration(event_level=logging.DEBUG)],
    )

    # I am not sure why, but "werkzeug" logger makes an INFO log on sending
    # the message "hi" and does creates an infinite look.
    # Ignoring this for breaking the infinite loop and still we can test
    # that our own log messages (sent from `_IGNORED_LOGGERS`) are not leading
    # to an infinite loop
    ignore_logger("werkzeug")

    sentry_sdk.get_global_scope().set_client(client)
    with isolation_scope():
        capture_message("hi")
        client.flush()

    assert len(capturing_server.captured) == 1


def test_transport_no_thread_on_shutdown_no_errors(capturing_server, make_client):
    client = make_client()

    # make it seem like the interpreter is shutting down
    with mock.patch(
        "threading.Thread.start",
        side_effect=RuntimeError("can't create new thread at interpreter shutdown"),
    ):
        sentry_sdk.get_global_scope().set_client(client)
        with isolation_scope():
            capture_message("hi")

    # nothing exploded but also no events can be sent anymore
    assert len(capturing_server.captured) == 0


NOW = datetime(2014, 6, 2)


@pytest.mark.parametrize(
    "input,expected",
    [
        # Invalid rate limits
        ("", {}),
        ("invalid", {}),
        (",,,", {}),
        (
            "42::organization, invalid, 4711:foobar;transaction;security:project",
            {
                None: NOW + timedelta(seconds=42),
                "transaction": NOW + timedelta(seconds=4711),
                "security": NOW + timedelta(seconds=4711),
                # Unknown data categories
                "foobar": NOW + timedelta(seconds=4711),
            },
        ),
        (
            "4711:foobar;;transaction:organization",
            {
                "transaction": NOW + timedelta(seconds=4711),
                # Unknown data categories
                "foobar": NOW + timedelta(seconds=4711),
                "": NOW + timedelta(seconds=4711),
            },
        ),
    ],
)
def test_parse_rate_limits(input, expected):
    assert dict(_parse_rate_limits(input, now=NOW)) == expected


def test_envelope_too_large_response(capturing_server, make_client):
    client = make_client()

    capturing_server.respond_with(code=413)
    client.capture_event({"type": "error"})
    client.capture_event({"type": "transaction"})
    client.flush()

    # Error, transaction, and client report payloads
    assert len(capturing_server.captured) == 3
    report = parse_json(capturing_server.captured[2].envelope.items[0].get_bytes())

    # Client reports for error, transaction and included span
    assert len(report["discarded_events"]) == 3
    assert {"reason": "send_error", "category": "error", "quantity": 1} in report[
        "discarded_events"
    ]
    assert {"reason": "send_error", "category": "span", "quantity": 1} in report[
        "discarded_events"
    ]
    assert {"reason": "send_error", "category": "transaction", "quantity": 1} in report[
        "discarded_events"
    ]

    capturing_server.clear_captured()


def test_simple_rate_limits(capturing_server, make_client):
    client = make_client()
    capturing_server.respond_with(code=429, headers={"Retry-After": "4"})

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(capturing_server.captured) == 1
    assert capturing_server.captured[0].path == "/api/132/envelope/"
    capturing_server.clear_captured()

    assert set(client.transport._disabled_until) == set([None])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "event"})
    client.flush()

    assert not capturing_server.captured


@pytest.mark.parametrize("response_code", [200, 429])
def test_data_category_limits(
    capturing_server, response_code, make_client, monkeypatch
):
    client = make_client(send_client_reports=False)

    captured_outcomes = []

    def record_lost_event(reason, data_category=None, item=None):
        if data_category is None:
            data_category = item.data_category
        return captured_outcomes.append((reason, data_category))

    monkeypatch.setattr(client.transport, "record_lost_event", record_lost_event)

    capturing_server.respond_with(
        code=response_code,
        headers={"X-Sentry-Rate-Limits": "4711:transaction:organization"},
    )

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(capturing_server.captured) == 1
    assert capturing_server.captured[0].path == "/api/132/envelope/"
    capturing_server.clear_captured()

    assert set(client.transport._disabled_until) == set(["transaction"])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "transaction"})
    client.flush()

    assert not capturing_server.captured

    client.capture_event({"type": "event"})
    client.flush()

    assert len(capturing_server.captured) == 1
    assert capturing_server.captured[0].path == "/api/132/envelope/"

    assert captured_outcomes == [
        ("ratelimit_backoff", "transaction"),
        ("ratelimit_backoff", "transaction"),
    ]


@pytest.mark.parametrize("response_code", [200, 429])
def test_data_category_limits_reporting(
    capturing_server, response_code, make_client, monkeypatch
):
    client = make_client(send_client_reports=True)

    capturing_server.respond_with(
        code=response_code,
        headers={
            "X-Sentry-Rate-Limits": "4711:transaction:organization, 4711:attachment:organization"
        },
    )

    outcomes_enabled = False
    real_fetch = client.transport._fetch_pending_client_report

    def intercepting_fetch(*args, **kwargs):
        if outcomes_enabled:
            return real_fetch(*args, **kwargs)

    monkeypatch.setattr(
        client.transport, "_fetch_pending_client_report", intercepting_fetch
    )
    # get rid of threading making things hard to track
    monkeypatch.setattr(client.transport._worker, "submit", lambda x: x() or True)

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(capturing_server.captured) == 1
    assert capturing_server.captured[0].path == "/api/132/envelope/"
    capturing_server.clear_captured()

    assert set(client.transport._disabled_until) == set(["attachment", "transaction"])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "transaction"})
    capturing_server.clear_captured()

    # flush out the events but don't flush the client reports
    client.flush()
    client.transport._last_client_report_sent = 0
    outcomes_enabled = True

    scope = get_isolation_scope()
    scope.add_attachment(bytes=b"Hello World", filename="hello.txt")
    client.capture_event({"type": "error"}, scope=scope)
    client.flush()

    # this goes out with an extra envelope because it's flushed after the last item
    # that is normally in the queue.  This is quite funny in a way because it means
    # that the envelope that caused its own over quota report (an error with an
    # attachment) will include its outcome since it's pending.
    assert len(capturing_server.captured) == 1
    envelope = capturing_server.captured[0].envelope
    assert envelope.items[0].type == "event"
    assert envelope.items[1].type == "client_report"
    report = parse_json(envelope.items[1].get_bytes())

    discarded_events = report["discarded_events"]

    assert len(discarded_events) == 3
    assert {
        "category": "transaction",
        "reason": "ratelimit_backoff",
        "quantity": 2,
    } in discarded_events
    assert {
        "category": "span",
        "reason": "ratelimit_backoff",
        "quantity": 2,
    } in discarded_events
    assert {
        "category": "attachment",
        "reason": "ratelimit_backoff",
        "quantity": 11,
    } in discarded_events

    capturing_server.clear_captured()

    # here we sent a normal event
    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "error", "release": "foo"})
    client.flush()

    assert len(capturing_server.captured) == 2

    assert len(capturing_server.captured[0].envelope.items) == 1
    event = capturing_server.captured[0].envelope.items[0].get_event()
    assert event["type"] == "error"
    assert event["release"] == "foo"

    envelope = capturing_server.captured[1].envelope
    assert envelope.items[0].type == "client_report"
    report = parse_json(envelope.items[0].get_bytes())

    discarded_events = report["discarded_events"]
    assert len(discarded_events) == 2
    assert {
        "category": "transaction",
        "reason": "ratelimit_backoff",
        "quantity": 1,
    } in discarded_events
    assert {
        "category": "span",
        "reason": "ratelimit_backoff",
        "quantity": 1,
    } in discarded_events


@pytest.mark.parametrize("response_code", [200, 429])
def test_complex_limits_without_data_category(
    capturing_server, response_code, make_client
):
    client = make_client()
    capturing_server.respond_with(
        code=response_code,
        headers={"X-Sentry-Rate-Limits": "4711::organization"},
    )

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(capturing_server.captured) == 1
    assert capturing_server.captured[0].path == "/api/132/envelope/"
    capturing_server.clear_captured()

    assert set(client.transport._disabled_until) == set([None])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "event"})
    client.flush()

    assert len(capturing_server.captured) == 0


@pytest.mark.parametrize("response_code", [200, 429])
@pytest.mark.parametrize(
    "item",
    [
        Item(payload=b"{}", type="log"),
        Item(
            type="log",
            content_type="application/vnd.sentry.items.log+json",
            headers={
                "item_count": 2,
            },
            payload=PayloadRef(
                json={
                    "items": [
                        {
                            "body": "This is a 'info' log...",
                            "level": "info",
                            "timestamp": datetime(
                                2025, 1, 1, tzinfo=timezone.utc
                            ).timestamp(),
                            "trace_id": "00000000-0000-0000-0000-000000000000",
                            "attributes": {
                                "sentry.environment": {
                                    "value": "production",
                                    "type": "string",
                                },
                                "sentry.release": {
                                    "value": "1.0.0",
                                    "type": "string",
                                },
                                "sentry.sdk.name": {
                                    "value": "sentry.python",
                                    "type": "string",
                                },
                                "sentry.sdk.version": {
                                    "value": "2.45.0",
                                    "type": "string",
                                },
                                "sentry.severity_number": {
                                    "value": 9,
                                    "type": "integer",
                                },
                                "sentry.severity_text": {
                                    "value": "info",
                                    "type": "string",
                                },
                                "server.address": {
                                    "value": "test-server",
                                    "type": "string",
                                },
                            },
                        },
                        {
                            "body": "The recorded value was '2.0'",
                            "level": "warn",
                            "timestamp": datetime(
                                2025, 1, 1, tzinfo=timezone.utc
                            ).timestamp(),
                            "trace_id": "00000000-0000-0000-0000-000000000000",
                            "attributes": {
                                "sentry.message.parameter.float_var": {
                                    "value": 2.0,
                                    "type": "double",
                                },
                                "sentry.message.template": {
                                    "value": "The recorded value was '{float_var}'",
                                    "type": "string",
                                },
                                "sentry.sdk.name": {
                                    "value": "sentry.python",
                                    "type": "string",
                                },
                                "sentry.sdk.version": {
                                    "value": "2.45.0",
                                    "type": "string",
                                },
                                "server.address": {
                                    "value": "test-server",
                                    "type": "string",
                                },
                                "sentry.environment": {
                                    "value": "production",
                                    "type": "string",
                                },
                                "sentry.release": {
                                    "value": "1.0.0",
                                    "type": "string",
                                },
                                "sentry.severity_number": {
                                    "value": 13,
                                    "type": "integer",
                                },
                                "sentry.severity_text": {
                                    "value": "warn",
                                    "type": "string",
                                },
                            },
                        },
                    ]
                }
            ),
        ),
    ],
)
def test_log_item_limits(capturing_server, response_code, item, make_client):
    client = make_client()
    capturing_server.respond_with(
        code=response_code,
        headers={
            "X-Sentry-Rate-Limits": "4711:log_item:organization:quota_exceeded:custom"
        },
    )

    envelope = Envelope()
    envelope.add_item(item)
    client.transport.capture_envelope(envelope)
    client.flush()

    assert len(capturing_server.captured) == 1
    assert capturing_server.captured[0].path == "/api/132/envelope/"
    capturing_server.clear_captured()

    assert set(client.transport._disabled_until) == {"log_item"}

    client.transport.capture_envelope(envelope)
    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(capturing_server.captured) == 2

    envelope = capturing_server.captured[0].envelope
    assert envelope.items[0].type == "transaction"
    envelope = capturing_server.captured[1].envelope
    assert envelope.items[0].type == "client_report"
    report = parse_json(envelope.items[0].get_bytes())

    assert {
        "category": "log_item",
        "reason": "ratelimit_backoff",
        "quantity": 1,
    } in report["discarded_events"]

    expected_lost_bytes = 1243
    if item.payload.bytes == b"{}":
        expected_lost_bytes = 2

    assert {
        "category": "log_byte",
        "reason": "ratelimit_backoff",
        "quantity": expected_lost_bytes,
    } in report["discarded_events"]


def test_hub_cls_backwards_compat():
    class TestCustomHubClass(Hub):
        pass

    transport = HttpTransport(
        defaultdict(lambda: None, {"dsn": "https://123abc@example.com/123"})
    )

    with pytest.deprecated_call():
        assert transport.hub_cls is Hub

    with pytest.deprecated_call():
        transport.hub_cls = TestCustomHubClass

    with pytest.deprecated_call():
        assert transport.hub_cls is TestCustomHubClass


@pytest.mark.parametrize("quantity", (1, 2, 10))
def test_record_lost_event_quantity(capturing_server, make_client, quantity):
    client = make_client()
    transport = client.transport

    transport.record_lost_event(reason="test", data_category="span", quantity=quantity)
    client.flush()

    (captured,) = capturing_server.captured  # Should only be one envelope
    envelope = captured.envelope
    (item,) = envelope.items  # Envelope should only have one item

    assert item.type == "client_report"

    report = parse_json(item.get_bytes())

    assert report["discarded_events"] == [
        {"category": "span", "reason": "test", "quantity": quantity}
    ]


@pytest.mark.parametrize("span_count", (0, 1, 2, 10))
def test_record_lost_event_transaction_item(capturing_server, make_client, span_count):
    client = make_client()
    transport = client.transport

    envelope = mock_transaction_envelope(span_count)
    (transaction_item,) = envelope.items

    transport.record_lost_event(reason="test", item=transaction_item)
    client.flush()

    (captured,) = capturing_server.captured  # Should only be one envelope
    envelope = captured.envelope
    (item,) = envelope.items  # Envelope should only have one item

    assert item.type == "client_report"

    report = parse_json(item.get_bytes())
    discarded_events = report["discarded_events"]

    assert len(discarded_events) == 2

    assert {
        "category": "transaction",
        "reason": "test",
        "quantity": 1,
    } in discarded_events

    assert {
        "category": "span",
        "reason": "test",
        "quantity": span_count + 1,
    } in discarded_events


def test_handle_unexpected_status_invokes_handle_request_error(
    make_client, monkeypatch
):
    client = make_client()
    transport = client.transport

    monkeypatch.setattr(transport._worker, "submit", lambda fn: fn() or True)

    def stub_request(method, endpoint, body=None, headers=None):
        class MockResponse:
            def __init__(self):
                self.status = 500  # Integer
                self.data = b"server error"
                self.headers = {}

            def close(self):
                pass

        return MockResponse()

    monkeypatch.setattr(transport, "_request", stub_request)

    seen = []
    monkeypatch.setattr(
        transport,
        "_handle_request_error",
        lambda envelope, loss_reason: seen.append(loss_reason),
    )

    client.capture_event({"message": "test"})
    client.flush()

    assert seen == ["status_500"]


def test_handle_request_error_basic_coverage(make_client, monkeypatch):
    client = make_client()
    transport = client.transport

    monkeypatch.setattr(transport._worker, "submit", lambda fn: fn() or True)

    # Track method calls
    calls = []

    def mock_on_dropped_event(reason):
        calls.append(("on_dropped_event", reason))

    def mock_record_lost_event(reason, data_category=None, item=None):
        calls.append(("record_lost_event", reason, data_category, item))

    monkeypatch.setattr(transport, "on_dropped_event", mock_on_dropped_event)
    monkeypatch.setattr(transport, "record_lost_event", mock_record_lost_event)

    # Test case 1: envelope is None
    transport._handle_request_error(envelope=None, loss_reason="test_reason")

    assert len(calls) == 2
    assert calls[0] == ("on_dropped_event", "test_reason")
    assert calls[1] == ("record_lost_event", "network_error", "error", None)

    # Reset
    calls.clear()

    # Test case 2: envelope with items
    envelope = Envelope()
    envelope.add_item(mock.MagicMock())  # Simple mock item
    envelope.add_item(mock.MagicMock())  # Another mock item

    transport._handle_request_error(envelope=envelope, loss_reason="connection_error")

    assert len(calls) == 3
    assert calls[0] == ("on_dropped_event", "connection_error")
    assert calls[1][0:2] == ("record_lost_event", "network_error")
    assert calls[2][0:2] == ("record_lost_event", "network_error")


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("client_flush_method", ["close", "flush"])
@pytest.mark.parametrize("use_pickle", (True, False))
@pytest.mark.parametrize("compression_level", (0, 9, None))
@pytest.mark.parametrize("compression_algo", ("gzip", "br", "<invalid>", None))
@pytest.mark.skipif(not PY38, reason="Async transport only supported in Python 3.8+")
async def test_transport_works_async(
    capturing_server,
    request,
    capsys,
    caplog,
    debug,
    make_client,
    client_flush_method,
    use_pickle,
    compression_level,
    compression_algo,
):
    caplog.set_level(logging.DEBUG)

    experiments = {}
    if compression_level is not None:
        experiments["transport_compression_level"] = compression_level

    if compression_algo is not None:
        experiments["transport_compression_algo"] = compression_algo

    # Enable async transport
    experiments["transport_async"] = True

    client = make_client(
        debug=debug,
        _experiments=experiments,
        integrations=[AsyncioIntegration()],
    )

    if use_pickle:
        client = pickle.loads(pickle.dumps(client))

    # Verify we're using async transport
    assert isinstance(client.transport, AsyncHttpTransport), (
        "Expected AsyncHttpTransport"
    )

    sentry_sdk.get_global_scope().set_client(client)
    request.addfinalizer(lambda: sentry_sdk.get_global_scope().set_client(None))

    add_breadcrumb(
        level="info", message="i like bread", timestamp=datetime.now(timezone.utc)
    )
    capture_message("löl")

    if client_flush_method == "close":
        await client.close_async(timeout=2.0)
    if client_flush_method == "flush":
        await client.flush_async(timeout=2.0)

    out, err = capsys.readouterr()
    assert not err and not out
    assert capturing_server.captured
    should_compress = (
        # default is to compress with brotli if available, gzip otherwise
        (compression_level is None)
        or (
            # setting compression level to 0 means don't compress
            compression_level > 0
        )
    ) and (
        # if we couldn't resolve to a known algo, we don't compress
        compression_algo != "<invalid>"
    )

    assert capturing_server.captured[0].compressed == should_compress
    # After flush, the worker task is still running, but the end of the test will shut down the event loop
    # Therefore, we need to explicitly close the client to clean up the worker task
    assert any("Sending envelope" in record.msg for record in caplog.records) == debug
    if client_flush_method == "flush":
        await client.close_async(timeout=2.0)


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_background_thread_capture(
    capturing_server, make_client, caplog
):
    """Test capture_envelope from background threads uses run_coroutine_threadsafe"""
    caplog.set_level(logging.DEBUG)
    experiments = {"transport_async": True}
    client = make_client(_experiments=experiments, integrations=[AsyncioIntegration()])
    assert isinstance(client.transport, AsyncHttpTransport)
    sentry_sdk.get_global_scope().set_client(client)
    captured_from_thread = []
    exception_from_thread = []

    def background_thread_work():
        try:
            # This should use run_coroutine_threadsafe path
            capture_message("from background thread")
            captured_from_thread.append(True)
        except Exception as e:
            exception_from_thread.append(e)

    thread = threading.Thread(target=background_thread_work)
    thread.start()
    thread.join()
    assert not exception_from_thread
    assert captured_from_thread
    await client.close_async(timeout=2.0)
    assert capturing_server.captured


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_event_loop_closed_scenario(
    capturing_server, make_client, caplog
):
    """Test behavior when trying to capture after event loop context ends"""
    caplog.set_level(logging.DEBUG)
    experiments = {"transport_async": True}
    client = make_client(_experiments=experiments, integrations=[AsyncioIntegration()])
    sentry_sdk.get_global_scope().set_client(client)
    original_loop = client.transport.loop

    with mock.patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
        with mock.patch.object(client.transport.loop, "is_running", return_value=False):
            with mock.patch("sentry_sdk.transport.logger") as mock_logger:
                # This should trigger the "no_async_context" path
                capture_message("after loop closed")

                mock_logger.warning.assert_called_with(
                    "Async Transport is not running in an event loop."
                )

    client.transport.loop = original_loop
    await client.close_async(timeout=2.0)


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_concurrent_requests(
    capturing_server, make_client, caplog
):
    """Test multiple simultaneous envelope submissions"""
    caplog.set_level(logging.DEBUG)
    experiments = {"transport_async": True}
    client = make_client(_experiments=experiments, integrations=[AsyncioIntegration()])
    assert isinstance(client.transport, AsyncHttpTransport)
    sentry_sdk.get_global_scope().set_client(client)

    num_messages = 15

    async def send_message(i):
        capture_message(f"concurrent message {i}")

    tasks = [send_message(i) for i in range(num_messages)]
    await asyncio.gather(*tasks)
    await client.close_async(timeout=2.0)
    assert len(capturing_server.captured) == num_messages


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_rate_limiting_with_concurrency(
    capturing_server, make_client, request
):
    """Test async transport rate limiting with concurrent requests"""
    experiments = {"transport_async": True}
    client = make_client(_experiments=experiments, integrations=[AsyncioIntegration()])

    assert isinstance(client.transport, AsyncHttpTransport)
    sentry_sdk.get_global_scope().set_client(client)
    request.addfinalizer(lambda: sentry_sdk.get_global_scope().set_client(None))
    capturing_server.respond_with(
        code=429, headers={"X-Sentry-Rate-Limits": "60:error:organization"}
    )

    # Send one request first to trigger rate limiting
    capture_message("initial message")
    await asyncio.sleep(0.1)  # Wait for request to execute
    assert client.transport._check_disabled("error") is True
    capturing_server.clear_captured()

    async def send_message(i):
        capture_message(f"message {i}")
        await asyncio.sleep(0.01)

    await asyncio.gather(*[send_message(i) for i in range(5)])
    await asyncio.sleep(0.1)
    # New request should be dropped due to rate limiting
    assert len(capturing_server.captured) == 0
    await client.close_async(timeout=2.0)


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_two_way_ssl_authentication():
    current_dir = os.path.dirname(__file__)
    cert_file = f"{current_dir}/test.pem"
    key_file = f"{current_dir}/test.key"

    client = Client(
        "https://foo@sentry.io/123",
        cert_file=cert_file,
        key_file=key_file,
        _experiments={"transport_async": True},
        integrations=[AsyncioIntegration()],
    )
    assert isinstance(client.transport, AsyncHttpTransport)

    options = client.transport._get_pool_options()
    assert options["ssl_context"] is not None

    await client.close_async()


# ============================================================================
# AsyncWorker unit tests
# ============================================================================


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_init():
    """Test AsyncWorker.__init__ sets up default state correctly."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=50)
    assert worker._queue is None
    assert worker._queue_size == 50
    assert worker._task is None
    assert worker._task_for_pid is None
    assert worker._loop is None
    assert worker._active_tasks == set()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_is_alive_not_started():
    """Test is_alive returns False before start()."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    assert worker.is_alive is False


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_is_alive_after_start():
    """Test is_alive returns True after start() in a running loop."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()
    assert worker.is_alive is True
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_is_alive_wrong_pid():
    """Test is_alive returns False when pid mismatches."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()
    # Simulate a fork by changing the pid
    worker._task_for_pid = -1
    assert worker.is_alive is False
    # Restore to clean up
    import os

    worker._task_for_pid = os.getpid()
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_is_alive_no_loop():
    """Test is_alive returns False when loop is None."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()
    worker._loop = None
    assert worker.is_alive is False


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_start_creates_queue_and_task():
    """Test start() creates asyncio queue and consumer task."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=10)
    worker.start()
    assert worker._queue is not None
    assert worker._queue.maxsize == 10
    assert worker._task is not None
    assert worker._loop is not None
    assert worker._task_for_pid == __import__("os").getpid()
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_start_no_running_loop():
    """Test start() handles no running event loop gracefully."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    with mock.patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
        worker.start()
    assert worker._loop is None
    assert worker._task is None
    assert worker._task_for_pid is None


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_start_reuses_existing_queue():
    """Test start() reuses existing queue if already created."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=10)
    worker.start()
    queue_ref = worker._queue
    # Kill and restart — queue should be reused
    worker.kill()
    worker.start()
    assert worker._queue is queue_ref
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_full_when_queue_is_none():
    """Test full() returns True when queue is None."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    assert worker.full() is True


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_full_when_not_full():
    """Test full() returns False when queue has capacity."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=10)
    worker.start()
    assert worker.full() is False
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_full_when_full():
    """Test full() returns True when queue is at capacity."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=1)
    worker.start()

    # Fill the queue (pause the consumer so it doesn't drain)
    async def slow_cb():
        await asyncio.sleep(100)

    worker.submit(slow_cb)
    # Give the consumer a moment to pick up the callback
    await asyncio.sleep(0.05)
    # Now the consumer is processing the slow_cb, and we put one more
    worker.submit(slow_cb)
    assert worker.full() is True
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_submit_and_process():
    """Test submit() queues a callback and it gets processed."""
    from sentry_sdk.worker import AsyncWorker

    results = []

    async def callback():
        results.append("done")

    worker = AsyncWorker()
    worker.start()
    assert worker.submit(callback) is True
    # Wait for processing
    await asyncio.sleep(0.1)
    assert results == ["done"]
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_submit_returns_false_when_queue_full():
    """Test submit() returns False when queue is full."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=1)
    worker.start()

    async def slow_cb():
        await asyncio.sleep(100)

    worker.submit(slow_cb)
    await asyncio.sleep(0.05)
    # Fill the queue
    worker.submit(slow_cb)
    # Now it's full
    assert worker.submit(slow_cb) is False
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_submit_returns_false_when_no_queue():
    """Test submit() returns False when no queue (no running loop during start)."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    # Don't start — queue is None
    # But _ensure_task calls start, so we need to mock start to do nothing
    with mock.patch.object(worker, "start"):
        assert worker.submit(lambda: None) is False


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_kill_cancels_tasks():
    """Test kill() cancels the main task and active callback tasks."""
    from sentry_sdk.worker import AsyncWorker

    results = []

    async def slow_callback():
        try:
            await asyncio.sleep(100)
            results.append("should_not_reach")
        except asyncio.CancelledError:
            results.append("cancelled")
            raise

    worker = AsyncWorker()
    worker.start()
    worker.submit(slow_callback)
    await asyncio.sleep(0.05)  # Let callback start

    assert len(worker._active_tasks) > 0
    worker.kill()
    await asyncio.sleep(0.05)  # Let cancellation propagate

    assert worker._task is None
    assert worker._loop is None
    assert worker._task_for_pid is None
    assert len(worker._active_tasks) == 0


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_kill_queue_full():
    """Test kill() handles QueueFull when adding terminator."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=1)
    worker.start()

    async def slow_cb():
        await asyncio.sleep(100)

    worker.submit(slow_cb)
    await asyncio.sleep(0.05)
    # Fill the queue
    worker.submit(slow_cb)
    # Now queue is full, kill should still work
    worker.kill()
    assert worker._task is None


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_kill_no_task():
    """Test kill() is a no-op when there's no task."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    # Should not raise
    worker.kill()
    assert worker._task is None


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_flush_returns_task():
    """Test flush() returns an asyncio task when alive."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()
    task = worker.flush(timeout=1.0)
    assert task is not None
    assert isinstance(task, asyncio.Task)
    await task
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_flush_returns_none_when_not_alive():
    """Test flush() returns None when worker is not alive."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    assert worker.flush(timeout=1.0) is None


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_flush_returns_none_zero_timeout():
    """Test flush() returns None when timeout is 0."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()
    assert worker.flush(timeout=0.0) is None
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_wait_flush_early_return_no_loop():
    """Test _wait_flush returns early if loop/queue is None."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    # _wait_flush should return immediately with no loop
    await worker._wait_flush(timeout=1.0)
    # No exception = success


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_wait_flush_with_callback():
    """Test _wait_flush calls callback on initial timeout."""
    from sentry_sdk.worker import AsyncWorker

    callback_calls = []

    def flush_callback(pending, timeout):
        callback_calls.append((pending, timeout))

    worker = AsyncWorker(queue_size=10)
    worker.start()

    async def slow_cb():
        await asyncio.sleep(10)

    worker.submit(slow_cb)
    await asyncio.sleep(0.05)  # Let consumer pick it up

    # Flush with very short initial timeout to trigger callback
    await worker._wait_flush(timeout=0.2, callback=flush_callback)
    assert len(callback_calls) >= 1
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_wait_flush_second_timeout():
    """Test _wait_flush logs error on second timeout."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker(queue_size=10)
    worker.start()

    async def very_slow_cb():
        await asyncio.sleep(100)

    worker.submit(very_slow_cb)
    await asyncio.sleep(0.05)

    # Both timeouts should expire
    with mock.patch("sentry_sdk.worker.logger") as mock_logger:
        await worker._wait_flush(timeout=0.15)
        mock_logger.error.assert_called()
        assert "flush timed out" in str(mock_logger.error.call_args)

    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_target_terminator():
    """Test _target exits on _TERMINATOR sentinel."""
    from sentry_sdk.worker import AsyncWorker, _TERMINATOR

    worker = AsyncWorker()
    worker.start()
    # Directly put terminator
    worker._queue.put_nowait(_TERMINATOR)
    # Wait for task to complete
    await asyncio.sleep(0.05)
    assert worker._task.done()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_target_with_none_queue():
    """Test _target returns immediately when queue is None."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker._queue = None
    # Should return immediately without error
    await worker._target()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_on_task_complete_cancelled_error():
    """Test _on_task_complete handles CancelledError gracefully."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()

    # Create a task that will be cancelled
    async def will_be_cancelled():
        await asyncio.sleep(100)

    task = asyncio.create_task(will_be_cancelled())
    worker._active_tasks.add(task)
    task.cancel()
    await asyncio.sleep(0.01)

    # Set queue to None to avoid task_done issues (we only care about CancelledError path)
    saved_queue = worker._queue
    worker._queue = None

    # _on_task_complete should handle CancelledError without logging error
    with mock.patch("sentry_sdk.worker.logger") as mock_logger:
        worker._on_task_complete(task)
        mock_logger.error.assert_not_called()

    # Verify task was discarded from active_tasks
    assert task not in worker._active_tasks

    worker._queue = saved_queue
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_on_task_complete_exception():
    """Test _on_task_complete logs error on exception."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()

    async def failing_cb():
        raise ValueError("test error")

    worker.submit(failing_cb)
    await asyncio.sleep(0.1)  # Let task complete

    # The error should have been logged
    # Check that the task was removed from active_tasks
    assert len(worker._active_tasks) == 0
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_on_task_complete_queue_none():
    """Test _on_task_complete handles queue being None (e.g., during shutdown)."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    worker.start()

    async def simple_cb():
        pass

    worker.submit(simple_cb)
    await asyncio.sleep(0.05)
    # Set queue to None before task_done is called
    # This tests the `if self._queue is not None` path
    worker._queue = None

    # Create a mock task that has a result
    mock_task = mock.MagicMock()
    mock_task.result.return_value = None
    worker._active_tasks.add(mock_task)

    worker._on_task_complete(mock_task)
    assert mock_task not in worker._active_tasks
    worker.kill()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="AsyncWorker requires Python 3.8+")
async def test_async_worker_ensure_task_calls_start():
    """Test _ensure_task calls start() when not alive."""
    from sentry_sdk.worker import AsyncWorker

    worker = AsyncWorker()
    assert worker.is_alive is False
    worker._ensure_task()
    assert worker.is_alive is True
    worker.kill()


# ============================================================================
# make_transport() async detection logic tests
# ============================================================================


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_make_transport_async_with_loop_and_integration():
    """Test make_transport selects AsyncHttpTransport when conditions are met."""
    from sentry_sdk.transport import make_transport, ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    options = {
        "dsn": "https://foo@sentry.io/123",
        "transport": None,
        "_experiments": {"transport_async": True},
        "integrations": [AsyncioIntegration()],
        "send_client_reports": True,
        "transport_queue_size": 100,
        "keep_alive": False,
        "socket_options": None,
        "ca_certs": None,
        "cert_file": None,
        "key_file": None,
        "http_proxy": None,
        "https_proxy": None,
        "proxy_headers": None,
    }
    transport = make_transport(options)
    assert isinstance(transport, AsyncHttpTransport)
    await transport._pool.aclose()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_make_transport_async_without_integration_falls_back():
    """Test make_transport falls back to sync when AsyncioIntegration is missing."""
    from sentry_sdk.transport import make_transport, ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    options = {
        "dsn": "https://foo@sentry.io/123",
        "transport": None,
        "_experiments": {"transport_async": True},
        "integrations": [],  # No AsyncioIntegration
        "send_client_reports": True,
        "transport_queue_size": 100,
        "keep_alive": False,
        "socket_options": None,
        "ca_certs": None,
        "cert_file": None,
        "key_file": None,
        "http_proxy": None,
        "https_proxy": None,
        "proxy_headers": None,
    }
    with mock.patch("sentry_sdk.transport.logger") as mock_logger:
        transport = make_transport(options)
    assert isinstance(transport, HttpTransport)
    mock_logger.warning.assert_any_call(
        "You tried to use AsyncHttpTransport but the AsyncioIntegration is not enabled. Falling back to sync transport."
    )


@skip_under_gevent
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
def test_make_transport_async_no_running_loop():
    """Test make_transport falls back to sync when no event loop is running."""
    from sentry_sdk.transport import make_transport, ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    options = {
        "dsn": "https://foo@sentry.io/123",
        "transport": None,
        "_experiments": {"transport_async": True},
        "integrations": [AsyncioIntegration()],
        "send_client_reports": True,
        "transport_queue_size": 100,
        "keep_alive": False,
        "socket_options": None,
        "ca_certs": None,
        "cert_file": None,
        "key_file": None,
        "http_proxy": None,
        "https_proxy": None,
        "proxy_headers": None,
    }
    with mock.patch("sentry_sdk.transport.logger") as mock_logger:
        transport = make_transport(options)
    assert isinstance(transport, HttpTransport)
    mock_logger.warning.assert_any_call(
        "No event loop running, falling back to sync transport."
    )


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_make_transport_async_with_http2_logs_warning():
    """Test make_transport logs warning when both http2 and async are requested."""
    from sentry_sdk.transport import make_transport, ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    options = {
        "dsn": "https://foo@sentry.io/123",
        "transport": None,
        "_experiments": {"transport_async": True, "transport_http2": True},
        "integrations": [AsyncioIntegration()],
        "send_client_reports": True,
        "transport_queue_size": 100,
        "keep_alive": False,
        "socket_options": None,
        "ca_certs": None,
        "cert_file": None,
        "key_file": None,
        "http_proxy": None,
        "https_proxy": None,
        "proxy_headers": None,
    }
    with mock.patch("sentry_sdk.transport.logger") as mock_logger:
        transport = make_transport(options)
    assert isinstance(transport, AsyncHttpTransport)
    mock_logger.warning.assert_any_call(
        "HTTP/2 transport is not supported with async transport. "
        "Ignoring transport_http2 experiment."
    )
    await transport._pool.aclose()


@skip_under_gevent
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
def test_make_transport_async_not_enabled():
    """Test make_transport falls back when ASYNC_TRANSPORT_ENABLED is False."""
    from sentry_sdk.transport import make_transport

    options = {
        "dsn": "https://foo@sentry.io/123",
        "transport": None,
        "_experiments": {"transport_async": True},
        "integrations": [AsyncioIntegration()],
        "send_client_reports": True,
        "transport_queue_size": 100,
        "keep_alive": False,
        "socket_options": None,
        "ca_certs": None,
        "cert_file": None,
        "key_file": None,
        "http_proxy": None,
        "https_proxy": None,
        "proxy_headers": None,
    }
    with mock.patch("sentry_sdk.transport.ASYNC_TRANSPORT_ENABLED", False):
        with mock.patch("sentry_sdk.transport.logger") as mock_logger:
            transport = make_transport(options)
        assert isinstance(transport, HttpTransport)
        mock_logger.warning.assert_any_call(
            "You tried to use AsyncHttpTransport but don't have httpcore[asyncio] installed. Falling back to sync transport."
        )


# ============================================================================
# HttpTransportCore shared method tests
# ============================================================================


def test_handle_request_error_with_custom_record_reason(make_client, monkeypatch):
    """Test _handle_request_error with custom record_reason parameter."""
    client = make_client()
    transport = client.transport

    calls = []

    def mock_on_dropped_event(reason):
        calls.append(("on_dropped_event", reason))

    def mock_record_lost_event(reason, data_category=None, item=None):
        calls.append(("record_lost_event", reason, data_category))

    monkeypatch.setattr(transport, "on_dropped_event", mock_on_dropped_event)
    monkeypatch.setattr(transport, "record_lost_event", mock_record_lost_event)

    transport._handle_request_error(
        envelope=None, loss_reason="status_413", record_reason="send_error"
    )

    assert calls[0] == ("on_dropped_event", "status_413")
    assert calls[1] == ("record_lost_event", "send_error", "error")


def test_handle_response_413(make_client, monkeypatch):
    """Test _handle_response for HTTP 413 status."""
    client = make_client()
    transport = client.transport

    error_calls = []
    monkeypatch.setattr(
        transport,
        "_handle_request_error",
        lambda envelope, loss_reason, record_reason="network_error": error_calls.append(
            (loss_reason, record_reason)
        ),
    )

    class MockResponse:
        status = 413
        headers = {}
        data = b"entity too large"

    transport._handle_response(MockResponse(), envelope=None)
    assert error_calls == [("status_413", "send_error")]


def test_handle_response_429(make_client, monkeypatch):
    """Test _handle_response for HTTP 429 status calls on_dropped_event."""
    client = make_client()
    transport = client.transport

    dropped_reasons = []
    monkeypatch.setattr(
        transport,
        "on_dropped_event",
        lambda reason: dropped_reasons.append(reason),
    )

    class MockResponse:
        status = 429
        headers = {"Retry-After": "60"}

    transport._handle_response(MockResponse(), envelope=None)
    assert dropped_reasons == ["status_429"]


def test_handle_response_other_error(make_client, monkeypatch):
    """Test _handle_response for other error status codes (e.g., 500)."""
    client = make_client()
    transport = client.transport

    error_calls = []
    monkeypatch.setattr(
        transport,
        "_handle_request_error",
        lambda envelope, loss_reason, record_reason="network_error": error_calls.append(
            loss_reason
        ),
    )

    class MockResponse:
        status = 500
        headers = {}
        data = b"server error"

    transport._handle_response(MockResponse(), envelope=None)
    assert error_calls == ["status_500"]


def test_handle_response_success(make_client, monkeypatch):
    """Test _handle_response for success codes does nothing extra."""
    client = make_client()
    transport = client.transport

    error_calls = []
    monkeypatch.setattr(
        transport,
        "_handle_request_error",
        lambda envelope, loss_reason, record_reason="network_error": error_calls.append(
            loss_reason
        ),
    )

    class MockResponse:
        status = 200
        headers = {}

    transport._handle_response(MockResponse(), envelope=None)
    assert error_calls == []


def test_update_headers(make_client):
    """Test _update_headers adds auth and user-agent headers."""
    client = make_client()
    transport = client.transport
    headers = {}
    transport._update_headers(headers)
    assert "User-Agent" in headers
    assert "X-Sentry-Auth" in headers
    assert "sentry.python" in headers["User-Agent"]


def test_prepare_envelope_removes_rate_limited_items(make_client, monkeypatch):
    """Test _prepare_envelope filters rate-limited items."""
    client = make_client()
    transport = client.transport

    # Rate-limit errors
    from datetime import datetime, timedelta, timezone

    transport._disabled_until["error"] = datetime.now(timezone.utc) + timedelta(hours=1)

    envelope = Envelope()
    envelope.add_event({"type": "error", "message": "test"})

    result = transport._prepare_envelope(envelope)
    # The only item was rate-limited, so result should be None
    assert result is None


def test_prepare_envelope_passes_non_rate_limited_items(make_client, monkeypatch):
    """Test _prepare_envelope keeps items that aren't rate-limited."""
    client = make_client()
    transport = client.transport

    envelope = Envelope()
    envelope.add_event({"type": "error", "message": "test"})

    result = transport._prepare_envelope(envelope)
    assert result is not None
    env, body, headers = result
    assert len(env.items) >= 1
    assert headers["Content-Type"] == "application/x-sentry-envelope"


def test_prepare_envelope_attaches_client_report(make_client, monkeypatch):
    """Test _prepare_envelope attaches pending client reports."""
    client = make_client()
    transport = client.transport

    # Add a discarded event to generate a client report
    transport._discarded_events[("error", "test_reason")] = 5
    transport._last_client_report_sent = 0  # Force the report to be fetched

    envelope = Envelope()
    envelope.add_event({"type": "error", "message": "test"})

    result = transport._prepare_envelope(envelope)
    assert result is not None
    env, body, headers = result
    # Should have the original item + client report
    types = [item.type for item in env.items]
    assert "client_report" in types


# ============================================================================
# AsyncHttpTransport-specific tests
# ============================================================================


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_get_header_value():
    """Test AsyncHttpTransport._get_header_value with httpcore-style headers."""
    from sentry_sdk.transport import ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    client = Client(
        "https://foo@sentry.io/123",
        _experiments={"transport_async": True},
        integrations=[AsyncioIntegration()],
    )
    assert isinstance(client.transport, AsyncHttpTransport)

    class MockResponse:
        headers = [
            (b"content-type", b"application/json"),
            (b"x-sentry-rate-limits", b"60:error:organization"),
        ]

    val = client.transport._get_header_value(MockResponse(), "X-Sentry-Rate-Limits")
    assert val == "60:error:organization"

    val = client.transport._get_header_value(MockResponse(), "Nonexistent-Header")
    assert val is None

    await client.close_async()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_capture_envelope_no_loop(caplog):
    """Test capture_envelope warns when loop is not running."""
    from sentry_sdk.transport import ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    client = Client(
        "https://foo@sentry.io/123",
        _experiments={"transport_async": True},
        integrations=[AsyncioIntegration()],
    )
    assert isinstance(client.transport, AsyncHttpTransport)

    envelope = Envelope()
    envelope.add_event({"type": "error", "message": "test"})

    with mock.patch.object(client.transport, "loop") as mock_loop:
        mock_loop.is_running.return_value = False
        with mock.patch("sentry_sdk.transport.logger") as mock_logger:
            client.transport.capture_envelope(envelope)
            mock_logger.warning.assert_called_with(
                "Async Transport is not running in an event loop."
            )

    await client.close_async()


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_kill_returns_task():
    """Test AsyncHttpTransport.kill() returns a pool cleanup task."""
    from sentry_sdk.transport import ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    client = Client(
        "https://foo@sentry.io/123",
        _experiments={"transport_async": True},
        integrations=[AsyncioIntegration()],
    )
    assert isinstance(client.transport, AsyncHttpTransport)

    kill_task = client.transport.kill()
    assert kill_task is not None
    assert isinstance(kill_task, asyncio.Task)
    await kill_task


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_kill_no_running_loop():
    """Test AsyncHttpTransport.kill() handles no running loop."""
    from sentry_sdk.transport import ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    client = Client(
        "https://foo@sentry.io/123",
        _experiments={"transport_async": True},
        integrations=[AsyncioIntegration()],
    )
    assert isinstance(client.transport, AsyncHttpTransport)

    with mock.patch.object(
        client.transport.loop, "create_task", side_effect=RuntimeError("no loop")
    ):
        result = client.transport.kill()
    assert result is None


@skip_under_gevent
@pytest.mark.asyncio
@pytest.mark.skipif(not PY38, reason="Async transport requires Python 3.8+")
async def test_async_transport_flush_returns_none_zero_timeout():
    """Test AsyncHttpTransport.flush() returns None for zero timeout."""
    from sentry_sdk.transport import ASYNC_TRANSPORT_ENABLED

    if not ASYNC_TRANSPORT_ENABLED:
        pytest.skip("httpcore[asyncio] not installed")

    client = Client(
        "https://foo@sentry.io/123",
        _experiments={"transport_async": True},
        integrations=[AsyncioIntegration()],
    )
    assert isinstance(client.transport, AsyncHttpTransport)

    result = client.transport.flush(timeout=0)
    assert result is None
    await client.close_async()

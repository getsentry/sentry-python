import logging
import pickle
import os
import socket
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from tests.conftest import CapturingServer

try:
    import httpcore
except (ImportError, ModuleNotFoundError):
    httpcore = None

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
from sentry_sdk.envelope import Envelope, Item, parse_json
from sentry_sdk.transport import (
    KEEP_ALIVE_SOCKET_OPTIONS,
    _parse_rate_limits,
    HttpTransport,
)
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger


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


def mock_transaction_envelope(span_count):
    # type: (int) -> Envelope
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
def test_log_item_limits(capturing_server, response_code, make_client):
    client = make_client()
    capturing_server.respond_with(
        code=response_code,
        headers={
            "X-Sentry-Rate-Limits": "4711:log_item:organization:quota_exceeded:custom"
        },
    )

    envelope = Envelope()
    envelope.add_item(Item(payload=b"{}", type="log"))
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
    assert report["discarded_events"] == [
        {"category": "log_item", "reason": "ratelimit_backoff", "quantity": 1},
    ]


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

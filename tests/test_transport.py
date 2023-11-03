# coding: utf-8
import logging
import pickle
import gzip
import io

from datetime import datetime, timedelta

import pytest
from collections import namedtuple
from werkzeug.wrappers import Request, Response

from pytest_localserver.http import WSGIServer

from sentry_sdk import Hub, Client, add_breadcrumb, capture_message, Scope
from sentry_sdk._compat import datetime_utcnow
from sentry_sdk.transport import _parse_rate_limits
from sentry_sdk.envelope import Envelope, parse_json
from sentry_sdk.integrations.logging import LoggingIntegration

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

CapturedData = namedtuple("CapturedData", ["path", "event", "envelope", "compressed"])


class CapturingServer(WSGIServer):
    def __init__(self, host="127.0.0.1", port=0, ssl_context=None):
        WSGIServer.__init__(self, host, port, self, ssl_context=ssl_context)
        self.code = 204
        self.headers = {}
        self.captured = []

    def respond_with(self, code=200, headers=None):
        self.code = code
        if headers:
            self.headers = headers

    def clear_captured(self):
        del self.captured[:]

    def __call__(self, environ, start_response):
        """
        This is the WSGI application.
        """
        request = Request(environ)
        event = envelope = None
        if request.headers.get("content-encoding") == "gzip":
            rdr = gzip.GzipFile(fileobj=io.BytesIO(request.data))
            compressed = True
        else:
            rdr = io.BytesIO(request.data)
            compressed = False

        if request.mimetype == "application/json":
            event = parse_json(rdr.read())
        else:
            envelope = Envelope.deserialize_from(rdr)

        self.captured.append(
            CapturedData(
                path=request.path,
                event=event,
                envelope=envelope,
                compressed=compressed,
            )
        )

        response = Response(status=self.code)
        response.headers.extend(self.headers)
        return response(environ, start_response)


@pytest.fixture
def capturing_server(request):
    server = CapturingServer()
    server.start()
    request.addfinalizer(server.stop)
    return server


@pytest.fixture
def make_client(request, capturing_server):
    def inner(**kwargs):
        return Client(
            "http://foobar@{}/132".format(capturing_server.url[len("http://") :]),
            **kwargs
        )

    return inner


@pytest.mark.forked
@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("client_flush_method", ["close", "flush"])
@pytest.mark.parametrize("use_pickle", (True, False))
@pytest.mark.parametrize("compressionlevel", (0, 9))
def test_transport_works(
    capturing_server,
    request,
    capsys,
    caplog,
    debug,
    make_client,
    client_flush_method,
    use_pickle,
    compressionlevel,
    maybe_monkeypatched_threading,
):
    caplog.set_level(logging.DEBUG)
    client = make_client(
        debug=debug,
        _experiments={
            "transport_zlib_compression_level": compressionlevel,
        },
    )

    if use_pickle:
        client = pickle.loads(pickle.dumps(client))

    Hub.current.bind_client(client)
    request.addfinalizer(lambda: Hub.current.bind_client(None))

    add_breadcrumb(level="info", message="i like bread", timestamp=datetime_utcnow())
    capture_message("löl")

    getattr(client, client_flush_method)()

    out, err = capsys.readouterr()
    assert not err and not out
    assert capturing_server.captured
    assert capturing_server.captured[0].compressed == (compressionlevel > 0)

    assert any("Sending event" in record.msg for record in caplog.records) == debug


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

    options = client.transport._get_pool_options([])
    assert options["num_pools"] == expected_num_pools


def test_transport_infinite_loop(capturing_server, request, make_client):
    client = make_client(
        debug=True,
        # Make sure we cannot create events from our own logging
        integrations=[LoggingIntegration(event_level=logging.DEBUG)],
    )

    with Hub(client):
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
        with Hub(client):
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


def test_simple_rate_limits(capturing_server, capsys, caplog, make_client):
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
    capturing_server, capsys, caplog, response_code, make_client, monkeypatch
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
    assert capturing_server.captured[0].path == "/api/132/store/"

    assert captured_outcomes == [
        ("ratelimit_backoff", "transaction"),
        ("ratelimit_backoff", "transaction"),
    ]


@pytest.mark.parametrize("response_code", [200, 429])
def test_data_category_limits_reporting(
    capturing_server, capsys, caplog, response_code, make_client, monkeypatch
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

    scope = Scope()
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
    assert sorted(report["discarded_events"], key=lambda x: x["quantity"]) == [
        {"category": "transaction", "reason": "ratelimit_backoff", "quantity": 2},
        {"category": "attachment", "reason": "ratelimit_backoff", "quantity": 11},
    ]
    capturing_server.clear_captured()

    # here we sent a normal event
    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "error", "release": "foo"})
    client.flush()

    assert len(capturing_server.captured) == 2

    event = capturing_server.captured[0].event
    assert event["type"] == "error"
    assert event["release"] == "foo"

    envelope = capturing_server.captured[1].envelope
    assert envelope.items[0].type == "client_report"
    report = parse_json(envelope.items[0].get_bytes())
    assert report["discarded_events"] == [
        {"category": "transaction", "reason": "ratelimit_backoff", "quantity": 1},
    ]


@pytest.mark.parametrize("response_code", [200, 429])
def test_complex_limits_without_data_category(
    capturing_server, capsys, caplog, response_code, make_client
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

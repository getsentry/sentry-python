# coding: utf-8
import time
import logging
import pickle
import json
import gzip
import io

from datetime import datetime, timedelta

import pytest

from sentry_sdk import Hub, Client, add_breadcrumb, capture_message, Scope
from sentry_sdk.transport import _parse_rate_limits
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.logging import LoggingIntegration


def get_event(request):
    return json.load(gzip.GzipFile(fileobj=io.BytesIO(request.data)))


def get_envelope(request):
    return Envelope.deserialize_from(gzip.GzipFile(fileobj=io.BytesIO(request.data)))


@pytest.fixture
def make_client(request, httpserver):
    def inner(**kwargs):
        return Client(
            "http://foobar@{}/132".format(httpserver.url[len("http://") :]), **kwargs
        )

    return inner


@pytest.mark.forked
@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("client_flush_method", ["close", "flush"])
@pytest.mark.parametrize("use_pickle", (True, False))
def test_transport_works(
    httpserver,
    request,
    capsys,
    caplog,
    debug,
    make_client,
    client_flush_method,
    use_pickle,
    maybe_monkeypatched_threading,
):
    httpserver.serve_content("ok", 200)
    caplog.set_level(logging.DEBUG)
    client = make_client(debug=debug)

    if use_pickle:
        client = pickle.loads(pickle.dumps(client))

    Hub.current.bind_client(client)
    request.addfinalizer(lambda: Hub.current.bind_client(None))

    add_breadcrumb(level="info", message="i like bread", timestamp=datetime.utcnow())
    capture_message("löl")

    getattr(client, client_flush_method)()

    out, err = capsys.readouterr()
    assert not err and not out
    assert httpserver.requests

    assert any("Sending event" in record.msg for record in caplog.records) == debug


def test_transport_infinite_loop(httpserver, request, make_client):
    httpserver.serve_content("ok", 200)

    client = make_client(
        debug=True,
        # Make sure we cannot create events from our own logging
        integrations=[LoggingIntegration(event_level=logging.DEBUG)],
    )

    with Hub(client):
        capture_message("hi")
        client.flush()

    assert len(httpserver.requests) == 1


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


def test_simple_rate_limits(httpserver, capsys, caplog, make_client):
    client = make_client()
    httpserver.serve_content("no", 429, headers={"Retry-After": "4"})

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(httpserver.requests) == 1
    assert httpserver.requests[0].url.endswith("/api/132/envelope/")
    del httpserver.requests[:]

    assert set(client.transport._disabled_until) == set([None])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "event"})
    client.flush()

    assert not httpserver.requests


@pytest.mark.parametrize("response_code", [200, 429])
def test_data_category_limits(
    httpserver, capsys, caplog, response_code, make_client, monkeypatch
):
    client = make_client(send_client_reports=False)

    captured_outcomes = []

    def record_lost_event(reason, data_category=None, item=None):
        if data_category is None:
            data_category = item.data_category
        return captured_outcomes.append((reason, data_category))

    monkeypatch.setattr(client.transport, "record_lost_event", record_lost_event)

    httpserver.serve_content(
        "hm",
        response_code,
        headers={"X-Sentry-Rate-Limits": "4711:transaction:organization"},
    )

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(httpserver.requests) == 1
    assert httpserver.requests[0].url.endswith("/api/132/envelope/")
    del httpserver.requests[:]

    assert set(client.transport._disabled_until) == set(["transaction"])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "transaction"})
    client.flush()

    assert not httpserver.requests

    client.capture_event({"type": "event"})
    client.flush()

    assert len(httpserver.requests) == 1
    assert httpserver.requests[0].url.endswith("/api/132/store/")

    assert captured_outcomes == [
        ("ratelimit_backoff", "transaction"),
        ("ratelimit_backoff", "transaction"),
    ]


@pytest.mark.parametrize("response_code", [200, 429])
def test_data_category_limits_reporting(
    httpserver, capsys, caplog, response_code, make_client, monkeypatch
):
    client = make_client(send_client_reports=True)

    httpserver.serve_content(
        "hm",
        response_code,
        headers={
            "X-Sentry-Rate-Limits": "4711:transaction:organization, 4711:attachment:organization"
        },
    )

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(httpserver.requests) == 1
    assert httpserver.requests[0].url.endswith("/api/132/envelope/")
    del httpserver.requests[:]

    assert set(client.transport._disabled_until) == set(["attachment", "transaction"])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "transaction"})
    del httpserver.requests[:]

    # now trick the transport to force flush sending out the stats with the
    # next envelope
    time.sleep(0.2)
    client.transport._last_client_report_sent = 0

    scope = Scope()
    scope.add_attachment(bytes=b"Hello World", filename="hello.txt")

    client.capture_event({"type": "error"}, scope=scope)
    client.flush()

    # this goes out with an extra envelope because it's flushed after the last item
    # that is normally in the queue.  This is quite funny in a way beacuse it means
    # that the envelope that caused its own over quota report (an error with an
    # attachment) will include its outcome since it's pending.
    assert len(httpserver.requests) == 1
    envelope = get_envelope(httpserver.requests[0])
    assert envelope.items[0].type == "event"
    assert envelope.items[1].type == "client_report"
    report = json.loads(envelope.items[1].get_bytes())
    assert report["discarded_events"] == [
        {"category": "transaction", "reason": "ratelimit_backoff", "quantity": 2},
        {"category": "attachment", "reason": "ratelimit_backoff", "quantity": 11},
    ]
    del httpserver.requests[:]

    # here we sent a normal event
    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "error", "release": "foo"})
    client.flush()

    assert len(httpserver.requests) == 2

    event = get_event(httpserver.requests[0])
    assert event["type"] == "error"
    assert event["release"] == "foo"

    envelope = get_envelope(httpserver.requests[1])
    assert envelope.items[0].type == "client_report"
    report = json.loads(envelope.items[0].get_bytes())
    assert report["discarded_events"] == [
        {"category": "transaction", "reason": "ratelimit_backoff", "quantity": 1},
    ]


@pytest.mark.parametrize("response_code", [200, 429])
def test_complex_limits_without_data_category(
    httpserver, capsys, caplog, response_code, make_client
):
    client = make_client()
    httpserver.serve_content(
        "hm",
        response_code,
        headers={"X-Sentry-Rate-Limits": "4711::organization"},
    )

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(httpserver.requests) == 1
    assert httpserver.requests[0].url.endswith("/api/132/envelope/")
    del httpserver.requests[:]

    assert set(client.transport._disabled_until) == set([None])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "event"})
    client.flush()

    assert len(httpserver.requests) == 0

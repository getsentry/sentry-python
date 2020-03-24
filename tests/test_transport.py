# coding: utf-8
import logging
import pickle

from datetime import datetime, timedelta

import pytest

from sentry_sdk import Hub, Client, add_breadcrumb, capture_message
from sentry_sdk.transport import _parse_rate_limits


@pytest.fixture(params=[True, False])
def make_client(request):
    def inner(*args, **kwargs):
        client = Client(*args, **kwargs)
        if request.param:
            client = pickle.loads(pickle.dumps(client))

        return client

    return inner


@pytest.mark.forked
@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("client_flush_method", ["close", "flush"])
def test_transport_works(
    httpserver,
    request,
    capsys,
    caplog,
    debug,
    make_client,
    client_flush_method,
    maybe_monkeypatched_threading,
):
    httpserver.serve_content("ok", 200)

    caplog.set_level(logging.DEBUG)

    client = make_client(
        "http://foobar@{}/123".format(httpserver.url[len("http://") :]), debug=debug
    )
    Hub.current.bind_client(client)
    request.addfinalizer(lambda: Hub.current.bind_client(None))

    add_breadcrumb(level="info", message="i like bread", timestamp=datetime.utcnow())
    capture_message("löl")

    getattr(client, client_flush_method)()

    out, err = capsys.readouterr()
    assert not err and not out
    assert httpserver.requests

    assert any("Sending event" in record.msg for record in caplog.records) == debug


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


def test_simple_rate_limits(httpserver, capsys, caplog):
    client = Client(dsn="http://foobar@{}/123".format(httpserver.url[len("http://") :]))
    httpserver.serve_content("no", 429, headers={"Retry-After": "4"})

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(httpserver.requests) == 1
    del httpserver.requests[:]

    assert set(client.transport._disabled_until) == set([None])

    client.capture_event({"type": "transaction"})
    client.capture_event({"type": "event"})
    client.flush()

    assert not httpserver.requests


@pytest.mark.parametrize("response_code", [200, 429])
def test_data_category_limits(httpserver, capsys, caplog, response_code):
    client = Client(
        dict(dsn="http://foobar@{}/123".format(httpserver.url[len("http://") :]))
    )
    httpserver.serve_content(
        "hm",
        response_code,
        headers={"X-Sentry-Rate-Limits": "4711:transaction:organization"},
    )

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(httpserver.requests) == 1
    del httpserver.requests[:]

    assert set(client.transport._disabled_until) == set(["transaction"])

    client.transport.capture_event({"type": "transaction"})
    client.transport.capture_event({"type": "transaction"})
    client.flush()

    assert not httpserver.requests

    client.capture_event({"type": "event"})
    client.flush()

    assert len(httpserver.requests) == 1


@pytest.mark.parametrize("response_code", [200, 429])
def test_complex_limits_without_data_category(
    httpserver, capsys, caplog, response_code
):
    client = Client(
        dict(dsn="http://foobar@{}/123".format(httpserver.url[len("http://") :]))
    )
    httpserver.serve_content(
        "hm", response_code, headers={"X-Sentry-Rate-Limits": "4711::organization"},
    )

    client.capture_event({"type": "transaction"})
    client.flush()

    assert len(httpserver.requests) == 1
    del httpserver.requests[:]

    assert set(client.transport._disabled_until) == set([None])

    client.transport.capture_event({"type": "transaction"})
    client.transport.capture_event({"type": "transaction"})
    client.capture_event({"type": "event"})
    client.flush()

    assert len(httpserver.requests) == 0

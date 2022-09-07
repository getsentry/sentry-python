import sys

from collections import Counter

import pytest
import sentry_sdk
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware, _looks_like_asgi3

async_asgi_testclient = pytest.importorskip("async_asgi_testclient")
from async_asgi_testclient import TestClient


minimum_python_36 = pytest.mark.skipif(
    sys.version_info < (3, 6), reason="ASGI is only supported in Python >= 3.6"
)


@pytest.fixture
def asgi3_app():
    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["route"] == "/trigger/error":
            division_by_zero = 1 / 0  # noqa

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                ],
            }
        )

        await send(
            {
                "type": "http.response.body",
                "body": b"Hello, world!",
            }
        )

    return app


@pytest.fixture
def asgi3_app_with_error():
    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                ],
            }
        )

        division_by_zero = 1 / 0  # noqa

        await send(
            {
                "type": "http.response.body",
                "body": b"Hello, world!",
            }
        )

    return app


@minimum_python_36
def test_invalid_transaction_style(asgi3_app):
    with pytest.raises(ValueError) as exp:
        SentryAsgiMiddleware(asgi3_app, transaction_style="URL")

    assert (
        str(exp.value)
        == "Invalid value for transaction_style: URL (must be in ('endpoint', 'url'))"
    )


@minimum_python_36
@pytest.mark.asyncio
async def test_capture_transaction(
    sentry_init,
    asgi3_app,
    capture_events,
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app)

    async with TestClient(app) as client:
        events = capture_events()
        await client.get("/?somevalue=123")

    (transaction_event,) = events

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "generic ASGI request"
    assert transaction_event["contexts"]["trace"]["op"] == "http.server"
    assert transaction_event["request"] == {
        "headers": {
            "host": "localhost",
            "remote-addr": "127.0.0.1",
            "user-agent": "ASGI-Test-Client",
        },
        "method": "GET",
        "query_string": "somevalue=123",
        "url": "http://localhost/",
    }


@minimum_python_36
@pytest.mark.asyncio
async def test_capture_transaction_with_error(
    sentry_init,
    asgi3_app_with_error,
    capture_events,
    DictionaryContaining,  # noqa: N803
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app_with_error)

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get("/")

    (error_event, transaction_event) = events

    assert error_event["transaction"] == "generic ASGI request"
    assert error_event["contexts"]["trace"]["op"] == "http.server"
    assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert error_event["exception"]["values"][0]["value"] == "division by zero"
    assert error_event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "asgi"

    assert transaction_event["type"] == "transaction"
    assert transaction_event["contexts"]["trace"] == DictionaryContaining(
        error_event["contexts"]["trace"]
    )
    assert transaction_event["contexts"]["trace"]["status"] == "internal_error"
    assert transaction_event["transaction"] == error_event["transaction"]
    assert transaction_event["request"] == error_event["request"]


@minimum_python_36
@pytest.mark.asyncio
async def test_websocket(
    sentry_init,
    asgi3_app_with_error,
    capture_events,
    DictionaryContaining,  # noqa: N803
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app_with_error)
    print(app)
    # TODO: implement


@minimum_python_36
@pytest.mark.asyncio
async def test_auto_session_tracking_with_aggregates(
    sentry_init, asgi3_app, capture_envelopes
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app)

    scope = {
        "endpoint": asgi3_app,
        "client": ("127.0.0.1", 60457),
    }
    with pytest.raises(ZeroDivisionError):
        envelopes = capture_envelopes()
        async with TestClient(app, scope=scope) as client:
            scope["route"] = "/some/fine/url"
            await client.get("/some/fine/url")
            scope["route"] = "/some/fine/url"
            await client.get("/some/fine/url")
            scope["route"] = "/trigger/error"
            await client.get("/trigger/error")

    sentry_sdk.flush()

    count_item_types = Counter()
    for envelope in envelopes:
        count_item_types[envelope.items[0].type] += 1

    assert count_item_types["transaction"] == 4
    assert count_item_types["event"] == 1
    assert count_item_types["sessions"] == 1
    assert len(envelopes) == 6

    session_aggregates = envelopes[-1].items[0].payload.json["aggregates"]
    assert session_aggregates[0]["exited"] == 3
    assert session_aggregates[0]["crashed"] == 1
    assert len(session_aggregates) == 1


@minimum_python_36
@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        (
            "/message",
            "url",
            "generic ASGI request",
            "route",
        ),
        (
            "/message",
            "endpoint",
            "tests.integrations.asgi.test_asgi.asgi3_app_with_error.<locals>.app",
            "component",
        ),
    ],
)
@pytest.mark.asyncio
async def test_transaction_style(
    sentry_init,
    asgi3_app_with_error,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(
        asgi3_app_with_error, transaction_style=transaction_style
    )

    scope = {
        "endpoint": asgi3_app_with_error,
        "route": url,
        "client": ("127.0.0.1", 60457),
    }

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app, scope=scope) as client:
            events = capture_events()
            await client.get(url)

    (_, transaction_event) = events

    assert transaction_event["transaction"] == expected_transaction
    assert transaction_event["transaction_info"] == {"source": expected_source}


def mock_asgi2_app():
    pass


class MockAsgi2App:
    def __call__():
        pass


class MockAsgi3App(MockAsgi2App):
    def __await__():
        pass

    async def __call__():
        pass


@minimum_python_36
def test_looks_like_asgi3(asgi3_app):
    # branch: inspect.isclass(app)
    assert _looks_like_asgi3(MockAsgi3App)
    assert not _looks_like_asgi3(MockAsgi2App)

    # branch: inspect.isfunction(app)
    assert _looks_like_asgi3(asgi3_app)
    assert not _looks_like_asgi3(mock_asgi2_app)

    # breanch: else
    asgi3 = MockAsgi3App()
    assert _looks_like_asgi3(asgi3)
    asgi2 = MockAsgi2App()
    assert not _looks_like_asgi3(asgi2)


@minimum_python_36
def test_get_ip_x_forwarded_for():
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "8.8.8.8"

    # x-forwarded-for overrides x-real-ip
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "8.8.8.8"

    # when multiple x-forwarded-for headers are, the first is taken
    headers = [
        (b"x-forwarded-for", b"5.5.5.5"),
        (b"x-forwarded-for", b"6.6.6.6"),
        (b"x-forwarded-for", b"7.7.7.7"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "5.5.5.5"


@minimum_python_36
def test_get_ip_x_real_ip():
    headers = [
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "10.10.10.10"

    # x-forwarded-for overrides x-real-ip
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "8.8.8.8"


@minimum_python_36
def test_get_ip():
    # if now headers are provided the ip is taken from the client.
    headers = []
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "127.0.0.1"

    # x-forwarded-for header overides the ip from client
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "8.8.8.8"

    # x-real-for header overides the ip from client
    headers = [
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    ip = middleware._get_ip(scope)
    assert ip == "10.10.10.10"


@minimum_python_36
def test_get_headers():
    headers = [
        (b"x-real-ip", b"10.10.10.10"),
        (b"some_header", b"123"),
        (b"some_header", b"abc"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    middleware = SentryAsgiMiddleware({})
    headers = middleware._get_headers(scope)
    assert headers == {
        "x-real-ip": "10.10.10.10",
        "some_header": "123, abc",
    }

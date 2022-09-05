import pytest
from async_asgi_testclient import TestClient
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware, _looks_like_asgi3


@pytest.fixture
def asgi3_app():
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


def test_invalid_transaction_style(asgi3_app):
    with pytest.raises(ValueError) as exp:
        SentryAsgiMiddleware(asgi3_app, transaction_style="URL")

    assert (
        str(exp.value)
        == "Invalid value for transaction_style: URL (must be in ('endpoint', 'url'))"
    )


@pytest.mark.asyncio
async def test_capture_transaction(
    sentry_init, asgi3_app, capture_events, DictionaryContaining
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


@pytest.mark.asyncio
async def test_capture_transaction_with_error(
    sentry_init, asgi3_app_with_error, capture_events, DictionaryContaining
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

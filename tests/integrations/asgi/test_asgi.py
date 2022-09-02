import pytest
from async_asgi_testclient import TestClient
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

# from unittest import mock


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


def test_warning_if_manual_setup_and_starlette():
    assert False


def test_run_asgi2():
    assert False


@pytest.mark.asyncio
# @mock.patch("sentry_sdk.integrations.asgi.SentryAsgiMiddleware._run_asgi3")
async def test_run_asgi3(asgi3_app):
    assert False


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
            "/message",
            "route",
        ),
        (
            "/message",
            "endpoint",
            "tests.integrations.starlette.test_starlette.starlette_app_factory.<locals>._message",
            "component",
        ),
        (
            "/message/123456",
            "url",
            "/message/{message_id}",
            "route",
        ),
        (
            "/message/123456",
            "endpoint",
            "tests.integrations.starlette.test_starlette.starlette_app_factory.<locals>._message_with_id",
            "component",
        ),
    ],
)
def test_transaction_style(
    sentry_init,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    # sentry_init(
    #     integrations=[StarletteIntegration(transaction_style=transaction_style)],
    # )
    assert False


def test_get_url():
    assert False


def test_get_query():
    assert False


def test_get_ip():
    assert False


def test_get_headers():
    assert False

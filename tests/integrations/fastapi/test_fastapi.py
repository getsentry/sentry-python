import pytest
from sentry_sdk.integrations.fastapi import FastApiIntegration

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk import capture_message
from sentry_sdk.integrations.starlette import StarletteIntegration


def fastapi_app_factory():
    app = FastAPI()

    @app.get("/message")
    async def _message():
        capture_message("Hi")
        return {"message": "Hi"}

    @app.get("/message/{message_id}")
    async def _message_with_id(message_id):
        capture_message("Hi")
        return {"message": "Hi"}

    return app


@pytest.mark.asyncio
async def test_response(sentry_init, capture_events):
    # FastAPI is heavily based on Starlette so we also need
    # to enable StarletteIntegration.
    # In the future this will be auto enabled.
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        debug=True,
    )

    app = fastapi_app_factory()

    events = capture_events()

    client = TestClient(app)
    response = client.get("/message")

    assert response.json() == {"message": "Hi"}

    assert len(events) == 2

    (message_event, transaction_event) = events
    assert message_event["message"] == "Hi"
    assert transaction_event["transaction"] == "/message"


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
            "tests.integrations.fastapi.test_fastapi.fastapi_app_factory.<locals>._message",
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
            "tests.integrations.fastapi.test_fastapi.fastapi_app_factory.<locals>._message_with_id",
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
    sentry_init(
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
            FastApiIntegration(transaction_style=transaction_style),
        ],
    )
    app = fastapi_app_factory()

    events = capture_events()

    client = TestClient(app)
    client.get(url)

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    (event,) = events

    assert "request" not in event
    assert "transaction" not in event

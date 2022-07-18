import pytest


fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentry_sdk import capture_message
from sentry_sdk.integrations.starlette import StarletteIntegration


def fastapi_app_factory():
    app = FastAPI()

    @app.get("/message")
    async def hi():
        capture_message("Hi")
        return {"message": "Hi"}

    @app.get("/message/{message_id}")
    async def hi_with_id(message_id):
        capture_message("Hi")
        return {"message": "Hi"}

    return app


@pytest.mark.asyncio
async def test_response(sentry_init, capture_events):
    # FastAPI is heavily based on Starlette so we just need to
    # enable StarletteIntegration and we are good to go.
    sentry_init(
        send_default_pii=True,
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration()],
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

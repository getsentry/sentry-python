import pytest


fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentry_sdk import capture_message
from sentry_sdk.integrations.fastapi import FastAPIIntegration


@pytest.fixture
def app():
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
async def test_response(sentry_init, app, capture_events):
    sentry_init(integrations=[FastAPIIntegration()])
    events = capture_events()

    client = TestClient(app)
    response = client.get("/message")

    assert len(events) == 0

    assert response.json() == {"message": "Hi"}

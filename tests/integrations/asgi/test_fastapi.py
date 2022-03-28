import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk import capture_message
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware


@pytest.fixture
def app():
    app = FastAPI()

    @app.get("/users/{user_id}")
    async def get_user(user_id: str):
        capture_message("hi", level="error")
        return {"user_id": user_id}

    app.add_middleware(SentryAsgiMiddleware, transaction_style="url")

    return app


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_fastapi_transaction_style(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    client = TestClient(app)
    response = client.get("/users/rick")

    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == "/users/{user_id}"
    assert event["request"]["env"] == {"REMOTE_ADDR": "testclient"}
    assert event["request"]["url"].endswith("/users/rick")
    assert event["request"]["method"] == "GET"

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    (event,) = events

    assert "request" not in event
    assert "transaction" not in event

import json
import threading

import pytest
from sentry_sdk.integrations.fastapi import FastApiIntegration

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk import capture_message
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


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

    @app.get("/sync/thread_ids")
    def _thread_ids_sync():
        return {
            "main": str(threading.main_thread().ident),
            "active": str(threading.current_thread().ident),
        }

    @app.get("/async/thread_ids")
    async def _thread_ids_async():
        return {
            "main": str(threading.main_thread().ident),
            "active": str(threading.current_thread().ident),
        }

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


def test_legacy_setup(
    sentry_init,
    capture_events,
):
    # Check that behaviour does not change
    # if the user just adds the new Integrations
    # and forgets to remove SentryAsgiMiddleware
    sentry_init()
    app = fastapi_app_factory()
    asgi_app = SentryAsgiMiddleware(app)

    events = capture_events()

    client = TestClient(asgi_app)
    client.get("/message/123456")

    (event,) = events
    assert event["transaction"] == "/message/{message_id}"


@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
@mock.patch("sentry_sdk.profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_active_thread_id(sentry_init, capture_envelopes, teardown_profiling, endpoint):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"profiles_sample_rate": 1.0},
    )
    app = fastapi_app_factory()
    asgi_app = SentryAsgiMiddleware(app)

    envelopes = capture_envelopes()

    client = TestClient(asgi_app)
    response = client.get(endpoint)
    assert response.status_code == 200

    data = json.loads(response.content)

    envelopes = [envelope for envelope in envelopes]
    assert len(envelopes) == 1

    profiles = [item for item in envelopes[0].items if item.type == "profile"]
    assert len(profiles) == 1

    for profile in profiles:
        transactions = profile.payload.json["transactions"]
        assert len(transactions) == 1
        assert str(data["active"]) == transactions[0]["active_thread_id"]

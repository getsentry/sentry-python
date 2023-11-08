import json
import logging
import threading

import pytest
from sentry_sdk.integrations.fastapi import FastApiIntegration

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sentry_sdk import capture_message
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def fastapi_app_factory():
    app = FastAPI()

    @app.get("/error")
    async def _error():
        capture_message("Hi")
        1 / 0
        return {"message": "Hi"}

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


@pytest.mark.asyncio
async def test_original_request_not_scrubbed(sentry_init, capture_events):
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        debug=True,
    )

    app = FastAPI()

    @app.post("/error")
    async def _error(request: Request):
        logging.critical("Oh no!")
        assert request.headers["Authorization"] == "Bearer ohno"
        assert await request.json() == {"password": "secret"}

        return {"error": "Oh no!"}

    events = capture_events()

    client = TestClient(app)
    client.post(
        "/error", json={"password": "secret"}, headers={"Authorization": "Bearer ohno"}
    )

    event = events[0]
    assert event["request"]["data"] == {"password": "[Filtered]"}
    assert event["request"]["headers"]["authorization"] == "[Filtered]"


@pytest.mark.asyncio
def test_response_status_code_ok_in_transaction_context(sentry_init, capture_envelopes):
    """
    Tests that the response status code is added to the transaction "response" context.
    """
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        release="demo-release",
    )

    envelopes = capture_envelopes()

    app = fastapi_app_factory()

    client = TestClient(app)
    client.get("/message")

    (_, transaction_envelope) = envelopes
    transaction = transaction_envelope.get_transaction_event()

    assert transaction["type"] == "transaction"
    assert len(transaction["contexts"]) > 0
    assert (
        "response" in transaction["contexts"].keys()
    ), "Response context not found in transaction"
    assert transaction["contexts"]["response"]["status_code"] == 200


@pytest.mark.asyncio
def test_response_status_code_error_in_transaction_context(
    sentry_init,
    capture_envelopes,
):
    """
    Tests that the response status code is added to the transaction "response" context.
    """
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        release="demo-release",
    )

    envelopes = capture_envelopes()

    app = fastapi_app_factory()

    client = TestClient(app)
    with pytest.raises(ZeroDivisionError):
        client.get("/error")

    (
        _,
        _,
        transaction_envelope,
    ) = envelopes
    transaction = transaction_envelope.get_transaction_event()

    assert transaction["type"] == "transaction"
    assert len(transaction["contexts"]) > 0
    assert (
        "response" in transaction["contexts"].keys()
    ), "Response context not found in transaction"
    assert transaction["contexts"]["response"]["status_code"] == 500


@pytest.mark.asyncio
def test_response_status_code_not_found_in_transaction_context(
    sentry_init,
    capture_envelopes,
):
    """
    Tests that the response status code is added to the transaction "response" context.
    """
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        release="demo-release",
    )

    envelopes = capture_envelopes()

    app = fastapi_app_factory()

    client = TestClient(app)
    client.get("/non-existing-route-123")

    (transaction_envelope,) = envelopes
    transaction = transaction_envelope.get_transaction_event()

    assert transaction["type"] == "transaction"
    assert len(transaction["contexts"]) > 0
    assert (
        "response" in transaction["contexts"].keys()
    ), "Response context not found in transaction"
    assert transaction["contexts"]["response"]["status_code"] == 404


@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "tests.integrations.fastapi.test_fastapi.fastapi_app_factory.<locals>._message_with_id",
            "component",
        ),
        (
            "/message/123456",
            "url",
            "/message/{message_id}",
            "route",
        ),
    ],
)
def test_transaction_name(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
    capture_envelopes,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
            FastApiIntegration(transaction_style=transaction_style),
        ],
        traces_sample_rate=1.0,
        debug=True,
    )

    envelopes = capture_envelopes()

    app = fastapi_app_factory()

    client = TestClient(app)
    client.get(request_url)

    (_, transaction_envelope) = envelopes
    transaction_event = transaction_envelope.get_transaction_event()

    assert transaction_event["transaction"] == expected_transaction_name
    assert (
        transaction_event["transaction_info"]["source"] == expected_transaction_source
    )


def test_route_endpoint_equal_dependant_call(sentry_init):
    """
    Tests that the route endpoint name is equal to the wrapped dependant call name.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
        ],
        traces_sample_rate=1.0,
        debug=True,
    )

    app = fastapi_app_factory()

    for route in app.router.routes:
        if not hasattr(route, "dependant"):
            continue
        assert route.endpoint.__qualname__ == route.dependant.call.__qualname__


@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "http://testserver/message/123456",
            "url",
        ),
        (
            "/message/123456",
            "url",
            "http://testserver/message/123456",
            "url",
        ),
    ],
)
def test_transaction_name_in_traces_sampler(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
):
    """
    Tests that a custom traces_sampler retrieves a meaningful transaction name.
    In this case the URL or endpoint, because we do not have the route yet.
    """

    def dummy_traces_sampler(sampling_context):
        assert (
            sampling_context["transaction_context"]["name"] == expected_transaction_name
        )
        assert (
            sampling_context["transaction_context"]["source"]
            == expected_transaction_source
        )

    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[StarletteIntegration(transaction_style=transaction_style)],
        traces_sampler=dummy_traces_sampler,
        traces_sample_rate=1.0,
        debug=True,
    )

    app = fastapi_app_factory()

    client = TestClient(app)
    client.get(request_url)


@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "starlette.middleware.trustedhost.TrustedHostMiddleware",
            "component",
        ),
        (
            "/message/123456",
            "url",
            "http://testserver/message/123456",
            "url",
        ),
    ],
)
def test_transaction_name_in_middleware(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
    capture_envelopes,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
            FastApiIntegration(transaction_style=transaction_style),
        ],
        traces_sample_rate=1.0,
        debug=True,
    )

    envelopes = capture_envelopes()

    app = fastapi_app_factory()

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "example.com",
        ],
    )

    client = TestClient(app)
    client.get(request_url)

    (transaction_envelope,) = envelopes
    transaction_event = transaction_envelope.get_transaction_event()

    assert transaction_event["contexts"]["response"]["status_code"] == 400
    assert transaction_event["transaction"] == expected_transaction_name
    assert (
        transaction_event["transaction_info"]["source"] == expected_transaction_source
    )

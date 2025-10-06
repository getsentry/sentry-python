import asyncio
import json
import logging
import pytest
import threading
import warnings
from unittest import mock

import fastapi
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from fastapi.middleware.trustedhost import TrustedHostMiddleware

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.feature_flags import add_feature_flag
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.utils import parse_version


FASTAPI_VERSION = parse_version(fastapi.__version__)

from tests.integrations.conftest import parametrize_test_configurable_status_codes
from tests.integrations.starlette import test_starlette

BODY_JSON = {"some": "json", "for": "testing", "nested": {"numbers": 123}}


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

    @app.delete("/nomessage")
    @app.get("/nomessage")
    @app.head("/nomessage")
    @app.options("/nomessage")
    @app.patch("/nomessage")
    @app.post("/nomessage")
    @app.put("/nomessage")
    @app.trace("/nomessage")
    async def _nomessage():
        return {"message": "nothing here..."}

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


def test_stream_available_in_handler(sentry_init):
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )

    app = FastAPI()

    @app.post("/consume")
    async def _consume_stream_body(request):
        # Avoid cache by constructing new request
        wrapped_request = Request(request.scope, request.receive)

        assert await asyncio.wait_for(wrapped_request.json(), timeout=1.0) == BODY_JSON

        return {"status": "ok"}

    client = TestClient(app)
    client.post(
        "/consume",
        json=BODY_JSON,
    )


@pytest.mark.asyncio
async def test_response(sentry_init, capture_events):
    # FastAPI is heavily based on Starlette so we also need
    # to enable StarletteIntegration.
    # In the future this will be auto enabled.
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
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
@mock.patch("sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_active_thread_id(sentry_init, capture_envelopes, teardown_profiling, endpoint):
    sentry_init(
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
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

    for item in profiles:
        transactions = item.payload.json["transactions"]
        assert len(transactions) == 1
        assert str(data["active"]) == transactions[0]["active_thread_id"]

    transactions = [item for item in envelopes[0].items if item.type == "transaction"]
    assert len(transactions) == 1

    for item in transactions:
        transaction = item.payload.json
        trace_context = transaction["contexts"]["trace"]
        assert str(data["active"]) == trace_context["data"]["thread.id"]


@pytest.mark.asyncio
async def test_original_request_not_scrubbed(sentry_init, capture_events):
    sentry_init(
        default_integrations=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
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
    assert "response" in transaction["contexts"].keys(), (
        "Response context not found in transaction"
    )
    assert transaction["contexts"]["response"]["status_code"] == 200


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
    assert "response" in transaction["contexts"].keys(), (
        "Response context not found in transaction"
    )
    assert transaction["contexts"]["response"]["status_code"] == 500


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
    assert "response" in transaction["contexts"].keys(), (
        "Response context not found in transaction"
    )
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


@test_starlette.parametrize_test_configurable_status_codes_deprecated
def test_configurable_status_codes_deprecated(
    sentry_init,
    capture_events,
    failed_request_status_codes,
    status_code,
    expected_error,
):
    with pytest.warns(DeprecationWarning):
        starlette_integration = StarletteIntegration(
            failed_request_status_codes=failed_request_status_codes
        )

    with pytest.warns(DeprecationWarning):
        fast_api_integration = FastApiIntegration(
            failed_request_status_codes=failed_request_status_codes
        )

    sentry_init(
        integrations=[
            starlette_integration,
            fast_api_integration,
        ]
    )

    events = capture_events()

    app = FastAPI()

    @app.get("/error")
    async def _error():
        raise HTTPException(status_code)

    client = TestClient(app)
    client.get("/error")

    if expected_error:
        assert len(events) == 1
    else:
        assert not events


@pytest.mark.skipif(
    FASTAPI_VERSION < (0, 80),
    reason="Requires FastAPI >= 0.80, because earlier versions do not support HTTP 'HEAD' requests",
)
def test_transaction_http_method_default(sentry_init, capture_events):
    """
    By default OPTIONS and HEAD requests do not create a transaction.
    """
    # FastAPI is heavily based on Starlette so we also need
    # to enable StarletteIntegration.
    # In the future this will be auto enabled.
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
        ],
    )

    app = fastapi_app_factory()

    events = capture_events()

    client = TestClient(app)
    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    assert len(events) == 1

    (event,) = events

    assert event["request"]["method"] == "GET"


@pytest.mark.skipif(
    FASTAPI_VERSION < (0, 80),
    reason="Requires FastAPI >= 0.80, because earlier versions do not support HTTP 'HEAD' requests",
)
def test_transaction_http_method_custom(sentry_init, capture_events):
    # FastAPI is heavily based on Starlette so we also need
    # to enable StarletteIntegration.
    # In the future this will be auto enabled.
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(
                http_methods_to_capture=(
                    "OPTIONS",
                    "head",
                ),  # capitalization does not matter
            ),
            FastApiIntegration(
                http_methods_to_capture=(
                    "OPTIONS",
                    "head",
                ),  # capitalization does not matter
            ),
        ],
    )

    app = fastapi_app_factory()

    events = capture_events()

    client = TestClient(app)
    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    assert len(events) == 2

    (event1, event2) = events

    assert event1["request"]["method"] == "OPTIONS"
    assert event2["request"]["method"] == "HEAD"


@parametrize_test_configurable_status_codes
def test_configurable_status_codes(
    sentry_init,
    capture_events,
    failed_request_status_codes,
    status_code,
    expected_error,
):
    integration_kwargs = {}
    if failed_request_status_codes is not None:
        integration_kwargs["failed_request_status_codes"] = failed_request_status_codes

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        starlette_integration = StarletteIntegration(**integration_kwargs)
        fastapi_integration = FastApiIntegration(**integration_kwargs)

    sentry_init(integrations=[starlette_integration, fastapi_integration])

    events = capture_events()

    app = FastAPI()

    @app.get("/error")
    async def _error():
        raise HTTPException(status_code)

    client = TestClient(app)
    client.get("/error")

    assert len(events) == int(expected_error)


@pytest.mark.parametrize("transaction_style", ["endpoint", "url"])
def test_app_host(sentry_init, capture_events, transaction_style):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
            FastApiIntegration(transaction_style=transaction_style),
        ],
    )

    app = FastAPI()
    subapp = FastAPI()

    @subapp.get("/subapp")
    async def subapp_route():
        return {"message": "Hello world!"}

    app.host("subapp", subapp)

    events = capture_events()

    client = TestClient(app)
    client.get("/subapp", headers={"Host": "subapp"})

    assert len(events) == 1

    (event,) = events
    assert "transaction" in event

    if transaction_style == "url":
        assert event["transaction"] == "/subapp"
    else:
        assert event["transaction"].endswith("subapp_route")


@pytest.mark.asyncio
async def test_feature_flags(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )

    events = capture_events()

    app = FastAPI()

    @app.get("/error")
    async def _error():
        add_feature_flag("hello", False)

        with sentry_sdk.start_span(name="test-span"):
            with sentry_sdk.start_span(name="test-span-2"):
                raise ValueError("something is wrong!")

    try:
        client = TestClient(app)
        client.get("/error")
    except ValueError:
        pass

    found = False
    for event in events:
        if "exception" in event.keys():
            assert event["contexts"]["flags"] == {
                "values": [
                    {"flag": "hello", "result": False},
                ]
            }
            found = True

    assert found, "No event with exception found"

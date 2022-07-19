from collections import Counter
import sys

import pytest
from sentry_sdk import Hub, capture_message, last_event_id
import sentry_sdk
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.fixture
def app():
    app = Starlette()

    @app.route("/sync-message")
    def hi(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    @app.route("/async-message")
    async def hi2(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    app.add_middleware(SentryAsgiMiddleware)

    return app


@pytest.fixture
def transaction_app():
    transaction_app = Starlette()

    @transaction_app.route("/sync-message")
    def hi(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    @transaction_app.route("/sync-message/{user_id:int}")
    def hi_with_id(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    @transaction_app.route("/async-message")
    async def async_hi(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    @transaction_app.route("/async-message/{user_id:int}")
    async def async_hi_with_id(request):
        capture_message("hi", level="error")
        return PlainTextResponse("ok")

    return transaction_app


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_sync_request_data(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    client = TestClient(app)
    response = client.get("/sync-message?foo=bar", headers={"Foo": "ä"})

    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == "generic ASGI request"
    assert event["request"]["env"] == {"REMOTE_ADDR": "testclient"}
    assert set(event["request"]["headers"]) == {
        "accept",
        "accept-encoding",
        "connection",
        "host",
        "user-agent",
        "foo",
    }
    assert event["request"]["query_string"] == "foo=bar"
    assert event["request"]["url"].endswith("/sync-message")
    assert event["request"]["method"] == "GET"

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    (event,) = events

    assert "request" not in event
    assert "transaction" not in event


def test_async_request_data(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    client = TestClient(app)
    response = client.get("/async-message?foo=bar")

    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == "generic ASGI request"
    assert event["request"]["env"] == {"REMOTE_ADDR": "testclient"}
    assert set(event["request"]["headers"]) == {
        "accept",
        "accept-encoding",
        "connection",
        "host",
        "user-agent",
    }
    assert event["request"]["query_string"] == "foo=bar"
    assert event["request"]["url"].endswith("/async-message")
    assert event["request"]["method"] == "GET"

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    (event,) = events

    assert "request" not in event
    assert "transaction" not in event


def test_errors(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    @app.route("/error")
    def myerror(request):
        raise ValueError("oh no")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/error")

    assert response.status_code == 500

    (event,) = events
    assert event["transaction"] == "generic ASGI request"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "ValueError"
    assert exception["value"] == "oh no"
    assert any(
        frame["filename"].endswith("tests/integrations/asgi/test_asgi.py")
        for frame in exception["stacktrace"]["frames"]
    )


def test_websocket(sentry_init, capture_events, request):
    sentry_init(debug=True, send_default_pii=True)

    # Bind client to main thread because context propagation for the websocket
    # client does not work.
    Hub.main.bind_client(Hub.current.client)
    request.addfinalizer(lambda: Hub.main.bind_client(None))

    events = capture_events()

    from starlette.testclient import TestClient

    def message():
        capture_message("hi")
        raise ValueError("oh no")

    async def app(scope, receive, send):
        assert scope["type"] == "websocket"
        websocket = WebSocket(scope, receive=receive, send=send)
        await websocket.accept()
        await websocket.send_text(message())
        await websocket.close()

    app = SentryAsgiMiddleware(app)

    client = TestClient(app)
    with client.websocket_connect("/") as websocket:
        with pytest.raises(ValueError):
            websocket.receive_text()

    msg_event, error_event = events

    assert msg_event["message"] == "hi"

    (exc,) = error_event["exception"]["values"]
    assert exc["type"] == "ValueError"
    assert exc["value"] == "oh no"

    assert (
        msg_event["request"]
        == error_event["request"]
        == {
            "env": {"REMOTE_ADDR": "testclient"},
            "headers": {
                "accept": "*/*",
                "accept-encoding": "gzip, deflate",
                "connection": "upgrade",
                "host": "testserver",
                "sec-websocket-key": "testserver==",
                "sec-websocket-version": "13",
                "user-agent": "testclient",
            },
            "method": None,
            "query_string": None,
            "url": "ws://testserver/",
        }
    )


def test_starlette_last_event_id(app, sentry_init, capture_events, request):
    sentry_init(send_default_pii=True)
    events = capture_events()

    @app.route("/handlederror")
    def handlederror(request):
        raise ValueError("oh no")

    @app.exception_handler(500)
    def handler(*args, **kwargs):
        return PlainTextResponse(last_event_id(), status_code=500)

    client = TestClient(SentryAsgiMiddleware(app), raise_server_exceptions=False)
    response = client.get("/handlederror")
    assert response.status_code == 500

    (event,) = events
    assert response.content.strip().decode("ascii") == event["event_id"]
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ValueError"
    assert exception["value"] == "oh no"


def test_transaction(app, sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @app.route("/tricks/kangaroo")
    def kangaroo_handler(request):
        return PlainTextResponse("dogs are great")

    client = TestClient(app)
    client.get("/tricks/kangaroo")

    event = events[0]
    assert event["type"] == "transaction"
    assert event["transaction"] == "generic ASGI request"


@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        (
            "/sync-message",
            "endpoint",
            "generic ASGI request",  # the AsgiMiddleware can not extract routes from the Starlette framework used here for testing.
            "route",
        ),
        (
            "/sync-message",
            "url",
            "generic ASGI request",  # the AsgiMiddleware can not extract routes from the Starlette framework used here for testing.
            "route",
        ),
        (
            "/sync-message/123456",
            "endpoint",
            "generic ASGI request",  # the AsgiMiddleware can not extract routes from the Starlette framework used here for testing.
            "route",
        ),
        (
            "/sync-message/123456",
            "url",
            "generic ASGI request",  # the AsgiMiddleware can not extract routes from the Starlette framework used here for testing.
            "route",
        ),
        (
            "/async-message",
            "endpoint",
            "generic ASGI request",  # the AsgiMiddleware can not extract routes from the Starlette framework used here for testing.
            "route",
        ),
        (
            "/async-message",
            "url",
            "generic ASGI request",  # the AsgiMiddleware can not extract routes from the Starlette framework used here for testing.
            "route",
        ),
    ],
)
def test_transaction_style(
    sentry_init,
    transaction_app,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
    capture_events,
):
    sentry_init(send_default_pii=True)

    transaction_app = SentryAsgiMiddleware(
        transaction_app, transaction_style=transaction_style
    )

    events = capture_events()

    client = TestClient(transaction_app)
    client.get(url)

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


def test_traces_sampler_gets_scope_in_sampling_context(
    app, sentry_init, DictionaryContaining  # noqa: N803
):
    traces_sampler = mock.Mock()
    sentry_init(traces_sampler=traces_sampler)

    @app.route("/tricks/kangaroo")
    def kangaroo_handler(request):
        return PlainTextResponse("dogs are great")

    client = TestClient(app)
    client.get("/tricks/kangaroo")

    traces_sampler.assert_any_call(
        DictionaryContaining(
            {
                # starlette just uses a dictionary to hold the scope
                "asgi_scope": DictionaryContaining(
                    {"method": "GET", "path": "/tricks/kangaroo"}
                )
            }
        )
    )


def test_x_forwarded_for(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    client = TestClient(app)
    response = client.get("/sync-message", headers={"X-Forwarded-For": "testproxy"})

    assert response.status_code == 200

    (event,) = events
    assert event["request"]["env"] == {"REMOTE_ADDR": "testproxy"}


def test_x_forwarded_for_multiple_entries(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    client = TestClient(app)
    response = client.get(
        "/sync-message", headers={"X-Forwarded-For": "testproxy1,testproxy2,testproxy3"}
    )

    assert response.status_code == 200

    (event,) = events
    assert event["request"]["env"] == {"REMOTE_ADDR": "testproxy1"}


def test_x_real_ip(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    client = TestClient(app)
    response = client.get("/sync-message", headers={"X-Real-IP": "1.2.3.4"})

    assert response.status_code == 200

    (event,) = events
    assert event["request"]["env"] == {"REMOTE_ADDR": "1.2.3.4"}


def test_auto_session_tracking_with_aggregates(app, sentry_init, capture_envelopes):
    """
    Test for correct session aggregates in auto session tracking.
    """

    @app.route("/dogs/are/great/")
    @app.route("/trigger/an/error/")
    def great_dogs_handler(request):
        if request["path"] != "/dogs/are/great/":
            1 / 0
        return PlainTextResponse("dogs are great")

    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()

    app = SentryAsgiMiddleware(app)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/dogs/are/great/")
    client.get("/dogs/are/great/")
    client.get("/trigger/an/error/")

    sentry_sdk.flush()

    count_item_types = Counter()
    for envelope in envelopes:
        count_item_types[envelope.items[0].type] += 1

    assert count_item_types["transaction"] == 3
    assert count_item_types["event"] == 1
    assert count_item_types["sessions"] == 1
    assert len(envelopes) == 5

    session_aggregates = envelopes[-1].items[0].payload.json["aggregates"]
    assert session_aggregates[0]["exited"] == 2
    assert session_aggregates[0]["crashed"] == 1
    assert len(session_aggregates) == 1

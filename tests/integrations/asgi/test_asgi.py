import sys

import pytest
from sentry_sdk import Hub, capture_message, last_event_id
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocket



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


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_sync_request_data(sentry_init, app, capture_events):
    sentry_init(send_default_pii=True)
    events = capture_events()

    client = TestClient(app)
    response = client.get("/sync-message?foo=bar", headers={"Foo": u"ä"})

    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == "tests.integrations.asgi.test_asgi.app.<locals>.hi"
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
    assert event["transaction"] == "tests.integrations.asgi.test_asgi.app.<locals>.hi2"
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
    assert (
        event["transaction"]
        == "tests.integrations.asgi.test_asgi.test_errors.<locals>.myerror"
    )
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

    event, = events
    assert response.content.strip().decode('ascii') == event['event_id']
    (exception,) = event["exception"]["values"]
    assert exception['type'] == 'ValueError'
    assert exception['value'] == 'oh no'

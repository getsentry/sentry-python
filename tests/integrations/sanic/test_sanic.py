import pytest

sanic = pytest.importorskip("sanic")

from sentry_sdk import capture_message
from sentry_sdk.integrations.sanic import SanicIntegration

from sanic import Sanic, response


@pytest.fixture
def app():
    app = Sanic(__name__)

    @app.route("/message")
    def hi(request):
        capture_message("hi")
        return response.text("ok")

    return app


def test_request_data(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    request, response = app.test_client.get("/message?foo=bar")
    assert response.status == 200

    event, = events
    assert event["transaction"] == "hi"
    assert event["request"]["env"] == {"REMOTE_ADDR": ""}
    assert set(event["request"]["headers"]) == {
        "accept",
        "accept-encoding",
        "host",
        "user-agent",
    }
    assert event["request"]["query_string"] == "foo=bar"
    assert event["request"]["url"].endswith("/message")
    assert event["request"]["method"] == "GET"

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    event, = events

    assert "request" not in event
    assert "transaction" not in event


def test_errors(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    @app.route("/error")
    def myerror(request):
        raise ValueError("oh no")

    request, response = app.test_client.get("/error")
    assert response.status == 500

    event, = events
    assert event["transaction"] == "myerror"
    exception, = event["exception"]["values"]

    assert exception["type"] == "ValueError"
    assert exception["value"] == "oh no"
    assert any(
        frame["filename"] == "test_sanic.py"
        for frame in exception["stacktrace"]["frames"]
    )


def test_error_in_errorhandler(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    @app.route("/error")
    async def myerror(request):
        raise ValueError("oh no")

    @app.exception(ValueError)
    async def myhandler(request, exception):
        1 / 0

    request, response = app.test_client.get("/error")
    assert response.status == 500

    event1, event2 = events

    exception, = event1["exception"]["values"]
    assert exception["type"] == "ValueError"
    assert any(
        frame["filename"] == "test_sanic.py"
        for frame in exception["stacktrace"]["frames"]
    )

    exception, = event2["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert any(
        frame["filename"] == "test_sanic.py"
        for frame in exception["stacktrace"]["frames"]
    )

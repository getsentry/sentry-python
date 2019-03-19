import random
import asyncio

import pytest

from sentry_sdk import capture_message, configure_scope
from sentry_sdk.integrations.sanic import SanicIntegration

from sanic import Sanic, request, response
from sanic.exceptions import abort


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
        frame["filename"].endswith("test_sanic.py")
        for frame in exception["stacktrace"]["frames"]
    )


def test_bad_request_not_captured(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    @app.route("/")
    def index(request):
        abort(400)

    request, response = app.test_client.get("/")
    assert response.status == 400

    assert not events


def test_error_in_errorhandler(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    @app.route("/error")
    def myerror(request):
        raise ValueError("oh no")

    @app.exception(ValueError)
    def myhandler(request, exception):
        1 / 0

    request, response = app.test_client.get("/error")
    assert response.status == 500

    event1, event2 = events

    exception, = event1["exception"]["values"]
    assert exception["type"] == "ValueError"
    assert any(
        frame["filename"].endswith("test_sanic.py")
        for frame in exception["stacktrace"]["frames"]
    )

    exception = event2["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"
    assert any(
        frame["filename"].endswith("test_sanic.py")
        for frame in exception["stacktrace"]["frames"]
    )


def test_concurrency(sentry_init, app):
    sentry_init(integrations=[SanicIntegration()])

    @app.route("/context-check/<i>")
    async def context_check(request, i):
        with configure_scope() as scope:
            scope.set_tag("i", i)

        await asyncio.sleep(random.random())

        with configure_scope() as scope:
            assert scope._tags["i"] == i

        return response.text("ok")

    async def task(i):
        responses = []

        await app.handle_request(
            request.Request(
                url_bytes=f"http://localhost/context-check/{i}".encode("ascii"),
                headers={},
                version="1.1",
                method="GET",
                transport=None,
            ),
            write_callback=responses.append,
            stream_callback=responses.append,
        )

        r, = responses
        assert r.status == 200

    async def runner():
        await asyncio.gather(*(task(i) for i in range(1000)))

    asyncio.run(runner())

    with configure_scope() as scope:
        assert not scope._tags

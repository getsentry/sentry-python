from __future__ import absolute_import

import logging

import pytest

pytest.importorskip("falcon")

import falcon
import falcon.testing
import sentry_sdk
from sentry_sdk.integrations.falcon import FalconIntegration
from sentry_sdk.integrations.logging import LoggingIntegration


try:
    import falcon.asgi
except ImportError:
    pass
else:
    import falcon.inspect  # We only need this module for the ASGI test


@pytest.fixture
def make_app(sentry_init):
    def inner():
        class MessageResource:
            def on_get(self, req, resp):
                sentry_sdk.capture_message("hi")
                resp.media = "hi"

        class MessageByIdResource:
            def on_get(self, req, resp, message_id):
                sentry_sdk.capture_message("hi")
                resp.media = "hi"

        app = falcon.API()
        app.add_route("/message", MessageResource())
        app.add_route("/message/{message_id:int}", MessageByIdResource())

        return app

    return inner


@pytest.fixture
def make_client(make_app):
    def inner():
        app = make_app()
        return falcon.testing.TestClient(app)

    return inner


def test_has_context(sentry_init, capture_events, make_client):
    sentry_init(integrations=[FalconIntegration()])
    events = capture_events()

    client = make_client()
    response = client.simulate_get("/message")
    assert response.status == falcon.HTTP_200

    (event,) = events
    assert event["transaction"] == "/message"  # Falcon URI template
    assert "data" not in event["request"]
    assert event["request"]["url"] == "http://falconframework.org/message"


@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        ("/message", "uri_template", "/message", "route"),
        ("/message", "path", "/message", "url"),
        ("/message/123456", "uri_template", "/message/{message_id:int}", "route"),
        ("/message/123456", "path", "/message/123456", "url"),
    ],
)
def test_transaction_style(
    sentry_init,
    make_client,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    integration = FalconIntegration(transaction_style=transaction_style)
    sentry_init(integrations=[integration])
    events = capture_events()

    client = make_client()
    response = client.simulate_get(url)
    assert response.status == falcon.HTTP_200

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


def test_unhandled_errors(sentry_init, capture_exceptions, capture_events):
    sentry_init(integrations=[FalconIntegration()], debug=True)

    class Resource:
        def on_get(self, req, resp):
            1 / 0

    app = falcon.API()
    app.add_route("/", Resource())

    exceptions = capture_exceptions()
    events = capture_events()

    client = falcon.testing.TestClient(app)

    try:
        client.simulate_get("/")
    except ZeroDivisionError:
        pass

    (exc,) = exceptions
    assert isinstance(exc, ZeroDivisionError)

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "falcon"
    assert " by zero" in event["exception"]["values"][0]["value"]


def test_raised_5xx_errors(sentry_init, capture_exceptions, capture_events):
    sentry_init(integrations=[FalconIntegration()], debug=True)

    class Resource:
        def on_get(self, req, resp):
            raise falcon.HTTPError(falcon.HTTP_502)

    app = falcon.API()
    app.add_route("/", Resource())

    exceptions = capture_exceptions()
    events = capture_events()

    client = falcon.testing.TestClient(app)
    client.simulate_get("/")

    (exc,) = exceptions
    assert isinstance(exc, falcon.HTTPError)

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "falcon"
    assert event["exception"]["values"][0]["type"] == "HTTPError"


def test_raised_4xx_errors(sentry_init, capture_exceptions, capture_events):
    sentry_init(integrations=[FalconIntegration()], debug=True)

    class Resource:
        def on_get(self, req, resp):
            raise falcon.HTTPError(falcon.HTTP_400)

    app = falcon.API()
    app.add_route("/", Resource())

    exceptions = capture_exceptions()
    events = capture_events()

    client = falcon.testing.TestClient(app)
    client.simulate_get("/")

    assert len(exceptions) == 0
    assert len(events) == 0


def test_http_status(sentry_init, capture_exceptions, capture_events):
    """
    This just demonstrates, that if Falcon raises a HTTPStatus with code 500
    (instead of a HTTPError with code 500) Sentry will not capture it.
    """
    sentry_init(integrations=[FalconIntegration()], debug=True)

    class Resource:
        def on_get(self, req, resp):
            raise falcon.http_status.HTTPStatus(falcon.HTTP_508)

    app = falcon.API()
    app.add_route("/", Resource())

    exceptions = capture_exceptions()
    events = capture_events()

    client = falcon.testing.TestClient(app)
    client.simulate_get("/")

    assert len(exceptions) == 0
    assert len(events) == 0


def test_falcon_large_json_request(sentry_init, capture_events):
    sentry_init(integrations=[FalconIntegration()])

    data = {"foo": {"bar": "a" * 2000}}

    class Resource:
        def on_post(self, req, resp):
            assert req.media == data
            sentry_sdk.capture_message("hi")
            resp.media = "ok"

    app = falcon.API()
    app.add_route("/", Resource())

    events = capture_events()

    client = falcon.testing.TestClient(app)
    response = client.simulate_post("/", json=data)
    assert response.status == falcon.HTTP_200

    (event,) = events
    assert event["_meta"]["request"]["data"]["foo"]["bar"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 1021, 1024]]}
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == 1024


@pytest.mark.parametrize("data", [{}, []], ids=["empty-dict", "empty-list"])
def test_falcon_empty_json_request(sentry_init, capture_events, data):
    sentry_init(integrations=[FalconIntegration()])

    class Resource:
        def on_post(self, req, resp):
            assert req.media == data
            sentry_sdk.capture_message("hi")
            resp.media = "ok"

    app = falcon.API()
    app.add_route("/", Resource())

    events = capture_events()

    client = falcon.testing.TestClient(app)
    response = client.simulate_post("/", json=data)
    assert response.status == falcon.HTTP_200

    (event,) = events
    assert event["request"]["data"] == data


def test_falcon_raw_data_request(sentry_init, capture_events):
    sentry_init(integrations=[FalconIntegration()])

    class Resource:
        def on_post(self, req, resp):
            sentry_sdk.capture_message("hi")
            resp.media = "ok"

    app = falcon.API()
    app.add_route("/", Resource())

    events = capture_events()

    client = falcon.testing.TestClient(app)
    response = client.simulate_post("/", body="hi")
    assert response.status == falcon.HTTP_200

    (event,) = events
    assert event["request"]["headers"]["Content-Length"] == "2"
    assert event["request"]["data"] == ""


def test_logging(sentry_init, capture_events):
    sentry_init(
        integrations=[FalconIntegration(), LoggingIntegration(event_level="ERROR")]
    )

    logger = logging.getLogger()

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            logger.error("hi")
            resp.media = "ok"

    app.add_route("/", Resource())

    events = capture_events()

    client = falcon.testing.TestClient(app)
    client.simulate_get("/")

    (event,) = events
    assert event["level"] == "error"


def test_500(sentry_init, capture_events):
    sentry_init(integrations=[FalconIntegration()])

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            1 / 0

    app.add_route("/", Resource())

    def http500_handler(ex, req, resp, params):
        sentry_sdk.capture_exception(ex)
        resp.media = {"message": "Sentry error: %s" % sentry_sdk.last_event_id()}

    app.add_error_handler(Exception, http500_handler)

    events = capture_events()

    client = falcon.testing.TestClient(app)
    response = client.simulate_get("/")

    (event,) = events
    assert response.json == {"message": "Sentry error: %s" % event["event_id"]}


def test_error_in_errorhandler(sentry_init, capture_events):
    sentry_init(integrations=[FalconIntegration()])

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            raise ValueError()

    app.add_route("/", Resource())

    def http500_handler(ex, req, resp, params):
        1 / 0

    app.add_error_handler(Exception, http500_handler)

    events = capture_events()

    client = falcon.testing.TestClient(app)

    with pytest.raises(ZeroDivisionError):
        client.simulate_get("/")

    (event,) = events

    last_ex_values = event["exception"]["values"][-1]
    assert last_ex_values["type"] == "ZeroDivisionError"
    assert last_ex_values["stacktrace"]["frames"][-1]["vars"]["ex"] == "ValueError()"


def test_bad_request_not_captured(sentry_init, capture_events):
    sentry_init(integrations=[FalconIntegration()])
    events = capture_events()

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            raise falcon.HTTPBadRequest()

    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    client.simulate_get("/")

    assert not events


def test_does_not_leak_scope(sentry_init, capture_events):
    sentry_init(integrations=[FalconIntegration()])
    events = capture_events()

    with sentry_sdk.configure_scope() as scope:
        scope.set_tag("request_data", False)

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            with sentry_sdk.configure_scope() as scope:
                scope.set_tag("request_data", True)

            def generator():
                for row in range(1000):
                    with sentry_sdk.configure_scope() as scope:
                        assert scope._tags["request_data"]

                    yield (str(row) + "\n").encode()

            resp.stream = generator()

    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)
    response = client.simulate_get("/")

    expected_response = "".join(str(row) + "\n" for row in range(1000))
    assert response.text == expected_response
    assert not events

    with sentry_sdk.configure_scope() as scope:
        assert not scope._tags["request_data"]


@pytest.mark.skipif(
    not hasattr(falcon, "asgi"), reason="This Falcon version lacks ASGI support."
)
def test_falcon_not_breaking_asgi(sentry_init):
    """
    This test simply verifies that the Falcon integration does not break ASGI
    Falcon apps.

    The test does not verify ASGI Falcon support, since our Falcon integration
    currently lacks support for ASGI Falcon apps.
    """
    sentry_init(integrations=[FalconIntegration()])

    asgi_app = falcon.asgi.App()

    try:
        falcon.inspect.inspect_app(asgi_app)
    except TypeError:
        pytest.fail("Falcon integration causing errors in ASGI apps.")

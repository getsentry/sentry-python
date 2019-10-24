import json
import pytest
import logging


pytest.importorskip("bottle")

from io import BytesIO
from bottle import Bottle, debug as set_debug, abort, redirect
from sentry_sdk import capture_message

from sentry_sdk.integrations.logging import LoggingIntegration
from werkzeug.test import Client

import sentry_sdk.integrations.bottle as bottle_sentry


@pytest.fixture(scope="function")
def app(sentry_init):
    app = Bottle()

    @app.route("/message")
    def hi():
        capture_message("hi")
        return "ok"

    @app.route("/message-named-route", name="hi")
    def named_hi():
        capture_message("hi")
        return "ok"

    yield app


@pytest.fixture
def get_client(app):
    def inner():
        return Client(app)

    return inner


def test_has_context(sentry_init, app, capture_events, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])
    events = capture_events()

    client = get_client()
    response = client.get("/message")
    assert response[1] == "200 OK"

    event, = events
    assert event["message"] == "hi"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "http://localhost/message"


@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction",
    [
        ("/message", "endpoint", "hi"),
        ("/message", "url", "/message"),
        ("/message-named-route", "endpoint", "hi"),
    ],
)
def test_transaction_style(
    sentry_init,
    app,
    capture_events,
    transaction_style,
    expected_transaction,
    url,
    get_client,
):
    sentry_init(
        integrations=[
            bottle_sentry.BottleIntegration(transaction_style=transaction_style)
        ]
    )
    events = capture_events()

    client = get_client()
    response = client.get("/message")
    assert response[1] == "200 OK"

    event, = events
    assert event["transaction"].endswith(expected_transaction)


@pytest.mark.parametrize("debug", (True, False), ids=["debug", "nodebug"])
@pytest.mark.parametrize("catchall", (True, False), ids=["catchall", "nocatchall"])
def test_errors(
    sentry_init, capture_exceptions, capture_events, app, debug, catchall, get_client
):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])

    app.catchall = catchall
    set_debug(mode=debug)

    exceptions = capture_exceptions()
    events = capture_events()

    @app.route("/")
    def index():
        1 / 0

    client = get_client()
    try:
        client.get("/")
    except ZeroDivisionError:
        pass

    exc, = exceptions
    assert isinstance(exc, ZeroDivisionError)

    event, = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "bottle"
    assert event["exception"]["values"][0]["mechanism"]["handled"] is False


def test_large_json_request(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])

    data = {"foo": {"bar": "a" * 2000}}

    @app.route("/", method="POST")
    def index():
        import bottle

        assert bottle.request.json == data
        assert bottle.request.body.read() == json.dumps(data).encode("ascii")
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = get_client()
    response = client.get("/")

    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response[1] == "200 OK"

    event, = events
    assert event["_meta"]["request"]["data"]["foo"]["bar"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == 512


@pytest.mark.parametrize("data", [{}, []], ids=["empty-dict", "empty-list"])
def test_empty_json_request(sentry_init, capture_events, app, data, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])

    @app.route("/", method="POST")
    def index():
        import bottle

        assert bottle.request.json == data
        assert bottle.request.body.read() == json.dumps(data).encode("ascii")
        # assert not bottle.request.forms
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = get_client()
    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response[1] == "200 OK"

    event, = events
    assert event["request"]["data"] == data


def test_medium_formdata_request(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])

    data = {"foo": "a" * 2000}

    @app.route("/", method="POST")
    def index():
        import bottle

        assert bottle.request.forms["foo"] == data["foo"]
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = get_client()
    response = client.post("/", data=data)
    assert response[1] == "200 OK"

    event, = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]) == 512


@pytest.mark.parametrize("input_char", [u"a", b"a"])
def test_too_large_raw_request(
    sentry_init, input_char, capture_events, app, get_client
):
    sentry_init(
        integrations=[bottle_sentry.BottleIntegration()], request_bodies="small"
    )

    data = input_char * 2000

    @app.route("/", method="POST")
    def index():
        import bottle

        if isinstance(data, bytes):
            assert bottle.request.body.read() == data
        else:
            assert bottle.request.body.read() == data.encode("ascii")
        assert not bottle.request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = get_client()
    response = client.post("/", data=data)
    assert response[1] == "200 OK"

    event, = events
    assert event["_meta"]["request"]["data"] == {
        "": {"len": 2000, "rem": [["!config", "x", 0, 2000]]}
    }
    assert not event["request"]["data"]


def test_files_and_form(sentry_init, capture_events, app, get_client):
    sentry_init(
        integrations=[bottle_sentry.BottleIntegration()], request_bodies="always"
    )

    data = {"foo": "a" * 2000, "file": (BytesIO(b"hello"), "hello.txt")}

    @app.route("/", method="POST")
    def index():
        import bottle

        assert list(bottle.request.forms) == ["foo"]
        assert list(bottle.request.files) == ["file"]
        assert not bottle.request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = get_client()
    response = client.post("/", data=data)
    assert response[1] == "200 OK"

    event, = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]) == 512

    assert event["_meta"]["request"]["data"]["file"] == {
        "": {
            "len": -1,
            "rem": [["!raw", "x", 0, -1]],
        }  # bottle default content-length is -1
    }
    assert not event["request"]["data"]["file"]


@pytest.mark.parametrize(
    "integrations",
    [
        [bottle_sentry.BottleIntegration()],
        [bottle_sentry.BottleIntegration(), LoggingIntegration(event_level="ERROR")],
    ],
)
def test_errors_not_reported_twice(
    sentry_init, integrations, capture_events, app, get_client
):
    sentry_init(integrations=integrations)

    app.catchall = False

    logger = logging.getLogger("bottle.app")

    @app.route("/")
    def index():
        try:
            1 / 0
        except Exception as e:
            logger.exception(e)
            raise e

    events = capture_events()

    client = get_client()
    with pytest.raises(ZeroDivisionError):
        client.get("/")

    assert len(events) == 1


def test_logging(sentry_init, capture_events, app, get_client):
    # ensure that Bottle's logger magic doesn't break ours
    sentry_init(
        integrations=[
            bottle_sentry.BottleIntegration(),
            LoggingIntegration(event_level="ERROR"),
        ]
    )

    @app.route("/")
    def index():
        app.logger.error("hi")
        return "ok"

    events = capture_events()

    client = get_client()
    client.get("/")

    event, = events
    assert event["level"] == "error"


def test_mount(app, capture_exceptions, capture_events, sentry_init, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])

    app.catchall = False

    def crashing_app(environ, start_response):
        1 / 0

    app.mount("/wsgi/", crashing_app)

    client = Client(app)

    exceptions = capture_exceptions()
    events = capture_events()

    with pytest.raises(ZeroDivisionError) as exc:
        client.get("/wsgi/")

    error, = exceptions

    assert error is exc.value

    event, = events
    assert event["exception"]["values"][0]["mechanism"] == {
        "type": "bottle",
        "handled": False,
    }


def test_500(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])

    set_debug(False)
    app.catchall = True

    @app.route("/")
    def index():
        1 / 0

    @app.error(500)
    def error_handler(err):
        capture_message("error_msg")
        return "My error"

    events = capture_events()

    client = get_client()
    response = client.get("/")
    assert response[1] == "500 Internal Server Error"

    _, event = events
    assert event["message"] == "error_msg"


def test_error_in_errorhandler(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])

    set_debug(False)
    app.catchall = True

    @app.route("/")
    def index():
        raise ValueError()

    @app.error(500)
    def error_handler(err):
        1 / 0

    events = capture_events()

    client = get_client()

    with pytest.raises(ZeroDivisionError):
        client.get("/")

    event1, event2 = events

    exception, = event1["exception"]["values"]
    assert exception["type"] == "ValueError"

    exception = event2["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_bad_request_not_captured(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])
    events = capture_events()

    @app.route("/")
    def index():
        abort(400, "bad request in")

    client = get_client()

    client.get("/")

    assert not events


def test_no_exception_on_redirect(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[bottle_sentry.BottleIntegration()])
    events = capture_events()

    @app.route("/")
    def index():
        redirect("/here")

    @app.route("/here")
    def here():
        return "here"

    client = get_client()

    client.get("/")

    assert not events

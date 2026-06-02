import json
import logging
from io import BytesIO

import pytest
from bottle import Bottle, HTTPResponse, abort, redirect
from bottle import debug as set_debug
from werkzeug.test import Client
from werkzeug.wrappers import Response

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.bottle import BottleIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.serializer import MAX_DATABAG_BREADTH


@pytest.fixture(scope="function")
def app(sentry_init):
    app = Bottle()

    @app.route("/message")
    def hi():
        capture_message("hi")
        return "ok"

    @app.route("/message/<message_id>")
    def hi_with_id(message_id):
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
    sentry_init(integrations=[BottleIntegration()])
    events = capture_events()

    client = get_client()
    response = client.get("/message")
    assert response[1] == "200 OK"

    (event,) = events
    assert event["message"] == "hi"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "http://localhost/message"


@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        ("/message", "endpoint", "hi", "component"),
        ("/message", "url", "/message", "route"),
        ("/message/123456", "url", "/message/<message_id>", "route"),
        ("/message-named-route", "endpoint", "hi", "component"),
    ],
)
def test_transaction_style(
    sentry_init,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
    capture_events,
    get_client,
):
    sentry_init(integrations=[BottleIntegration(transaction_style=transaction_style)])
    events = capture_events()

    client = get_client()
    response = client.get(url)
    assert response[1] == "200 OK"

    (event,) = events
    # We use endswith() because in Python 2.7 it is "test_bottle.hi"
    # and in later Pythons "test_bottle.app.<locals>.hi"
    assert event["transaction"].endswith(expected_transaction)
    assert event["transaction_info"] == {"source": expected_source}


@pytest.mark.parametrize("debug", (True, False), ids=["debug", "nodebug"])
@pytest.mark.parametrize("catchall", (True, False), ids=["catchall", "nocatchall"])
def test_errors(
    sentry_init, capture_exceptions, capture_events, app, debug, catchall, get_client
):
    sentry_init(integrations=[BottleIntegration()])

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

    (exc,) = exceptions
    assert isinstance(exc, ZeroDivisionError)

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "bottle"
    assert event["exception"]["values"][0]["mechanism"]["handled"] is False


@pytest.mark.parametrize("max_value_length", [1024, None])
def test_large_json_request(
    sentry_init, capture_events, app, get_client, max_value_length
):
    sentry_init(
        integrations=[BottleIntegration()],
        max_request_body_size="always",
        max_value_length=max_value_length,
    )

    data = {"foo": {"bar": "a" * (1034)}}

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

    (event,) = events

    if max_value_length:
        assert event["_meta"]["request"]["data"]["foo"]["bar"] == {
            "": {
                "len": 1034,
                "rem": [["!limit", "x", 1021, 1024]],
            }
        }
        assert len(event["request"]["data"]["foo"]["bar"]) == 1024
    else:
        assert len(event["request"]["data"]["foo"]["bar"]) == 1034


@pytest.mark.parametrize("data", [{}, []], ids=["empty-dict", "empty-list"])
def test_empty_json_request(sentry_init, capture_events, app, data, get_client):
    sentry_init(integrations=[BottleIntegration()])

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

    (event,) = events
    assert event["request"]["data"] == data


@pytest.mark.parametrize("max_value_length", [1024, None])
def test_medium_formdata_request(
    sentry_init, capture_events, app, get_client, max_value_length
):
    sentry_init(
        integrations=[BottleIntegration()],
        max_request_body_size="always",
        max_value_length=max_value_length,
    )

    data = {"foo": "a" * (1034)}

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

    (event,) = events

    if max_value_length:
        assert event["_meta"]["request"]["data"]["foo"] == {
            "": {
                "len": 1034,
                "rem": [["!limit", "x", 1021, 1024]],
            }
        }
        assert len(event["request"]["data"]["foo"]) == 1024
    else:
        assert len(event["request"]["data"]["foo"]) == 1034


@pytest.mark.parametrize("input_char", ["a", b"a"])
def test_too_large_raw_request(
    sentry_init, input_char, capture_events, app, get_client
):
    sentry_init(integrations=[BottleIntegration()], max_request_body_size="small")

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

    (event,) = events
    assert event["_meta"]["request"]["data"] == {"": {"rem": [["!config", "x"]]}}
    assert not event["request"]["data"]


@pytest.mark.parametrize("max_value_length", [1024, None])
def test_files_and_form(sentry_init, capture_events, app, get_client, max_value_length):
    sentry_init(
        integrations=[BottleIntegration()],
        max_request_body_size="always",
        max_value_length=max_value_length,
    )

    data = {
        "foo": "a" * (1034),
        "file": (BytesIO(b"hello"), "hello.txt"),
    }

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

    (event,) = events
    if max_value_length:
        assert event["_meta"]["request"]["data"]["foo"] == {
            "": {
                "len": 1034,
                "rem": [["!limit", "x", 1021, 1024]],
            }
        }
        assert len(event["request"]["data"]["foo"]) == 1024
    else:
        assert len(event["request"]["data"]["foo"]) == 1034

    assert event["_meta"]["request"]["data"]["file"] == {
        "": {
            "rem": [["!raw", "x"]],
        }
    }
    assert not event["request"]["data"]["file"]


def test_json_not_truncated_if_max_request_body_size_is_always(
    sentry_init, capture_events, app, get_client
):
    sentry_init(integrations=[BottleIntegration()], max_request_body_size="always")

    data = {
        "key{}".format(i): "value{}".format(i) for i in range(MAX_DATABAG_BREADTH + 10)
    }

    @app.route("/", method="POST")
    def index():
        import bottle

        assert bottle.request.json == data
        assert bottle.request.body.read() == json.dumps(data).encode("ascii")
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = get_client()

    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response[1] == "200 OK"

    (event,) = events
    assert event["request"]["data"] == data


@pytest.mark.parametrize(
    "integrations",
    [
        [BottleIntegration()],
        [BottleIntegration(), LoggingIntegration(event_level="ERROR")],
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
        1 / 0

    events = capture_events()

    client = get_client()

    with pytest.raises(ZeroDivisionError):
        try:
            client.get("/")
        except ZeroDivisionError as e:
            logger.exception(e)
            raise e

    assert len(events) == 1


def test_mount(app, capture_exceptions, capture_events, sentry_init, get_client):
    sentry_init(integrations=[BottleIntegration()])

    app.catchall = False

    def crashing_app(environ, start_response):
        1 / 0

    app.mount("/wsgi/", crashing_app)

    client = Client(app)

    exceptions = capture_exceptions()
    events = capture_events()

    with pytest.raises(ZeroDivisionError) as exc:
        client.get("/wsgi/")

    (error,) = exceptions

    assert error is exc.value

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "bottle"
    assert event["exception"]["values"][0]["mechanism"]["handled"] is False


def test_error_in_errorhandler(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[BottleIntegration()])

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

    (exception,) = event1["exception"]["values"]
    assert exception["type"] == "ValueError"

    exception = event2["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_bad_request_not_captured(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[BottleIntegration()])
    events = capture_events()

    @app.route("/")
    def index():
        abort(400, "bad request in")

    client = get_client()

    client.get("/")

    assert not events


def test_no_exception_on_redirect(sentry_init, capture_events, app, get_client):
    sentry_init(integrations=[BottleIntegration()])
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    sentry_init,
    get_client,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[BottleIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client = get_client()
    client.get("/message")

    if span_streaming:
        sentry_sdk.flush()
        spans = [item.payload for item in items]
        segment = spans[-1]
        assert segment["is_segment"] is True
        assert segment["attributes"]["sentry.origin"] == "auto.http.bottle"
    else:
        (_, event) = events
        assert event["contexts"]["trace"]["origin"] == "auto.http.bottle"


@pytest.mark.parametrize("raise_error", [True, False])
@pytest.mark.parametrize(
    ("integration_kwargs", "status_code", "should_capture"),
    (
        ({}, None, False),
        ({}, 400, False),
        ({}, 451, False),  # Highest 4xx status code
        ({}, 500, True),
        ({}, 511, True),  # Highest 5xx status code
        ({"failed_request_status_codes": set()}, 500, False),
        ({"failed_request_status_codes": set()}, 511, False),
        ({"failed_request_status_codes": {404, *range(500, 600)}}, 404, True),
        ({"failed_request_status_codes": {404, *range(500, 600)}}, 500, True),
        ({"failed_request_status_codes": {404, *range(500, 600)}}, 400, False),
    ),
)
def test_failed_request_status_codes(
    sentry_init,
    capture_events,
    integration_kwargs,
    status_code,
    should_capture,
    raise_error,
):
    sentry_init(integrations=[BottleIntegration(**integration_kwargs)])
    events = capture_events()

    app = Bottle()

    @app.route("/")
    def handle():
        if status_code is not None:
            response = HTTPResponse(status=status_code)
            if raise_error:
                raise response
            else:
                return response
        return "OK"

    client = Client(app, Response)
    response = client.get("/")

    expected_status = 200 if status_code is None else status_code
    assert response.status_code == expected_status

    if should_capture:
        (event,) = events
        assert event["exception"]["values"][0]["type"] == "HTTPResponse"
    else:
        assert not events


def test_failed_request_status_codes_non_http_exception(sentry_init, capture_events):
    """
    If an exception, which is not an instance of HTTPResponse, is raised, it should be captured, even if
    failed_request_status_codes is empty.
    """
    sentry_init(integrations=[BottleIntegration(failed_request_status_codes=set())])
    events = capture_events()

    app = Bottle()

    @app.route("/")
    def handle():
        1 / 0

    client = Client(app, Response)

    try:
        client.get("/")
    except ZeroDivisionError:
        pass

    (event,) = events
    assert event["exception"]["values"][0]["type"] == "ZeroDivisionError"


def test_span_streaming_basic(sentry_init, capture_items):
    sentry_init(
        integrations=[BottleIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("span")

    app = Bottle()

    @app.route("/message")
    def hi():
        return "ok"

    client = Client(app)
    client.get("/message")

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    assert len(spans) == 3

    callback_span = spans[0]
    handle_span = spans[1]
    segment = spans[2]

    # Segment span (root, created by WSGI middleware)
    assert segment["is_segment"] is True
    assert "parent_span_id" not in segment
    assert segment["status"] == "ok"
    assert segment["attributes"]["sentry.op"] == "http.server"
    assert segment["attributes"]["sentry.origin"] == "auto.http.bottle"
    assert segment["attributes"]["http.request.method"] == "GET"
    assert segment["attributes"]["http.response.status_code"] == 200

    # Handle span (created by _patched_handle, child of segment)
    assert handle_span["is_segment"] is False
    assert handle_span["parent_span_id"] == segment["span_id"]
    assert handle_span["name"].endswith("hi")

    # Callback span (created by wrapped_callback, child of handle span)
    assert callback_span["is_segment"] is False
    assert callback_span["parent_span_id"] == handle_span["span_id"]
    assert callback_span["name"] == "bottle"


@pytest.mark.parametrize(
    "url,transaction_style,expected_name,expected_source",
    [
        ("/message", "endpoint", "hi", "component"),
        ("/message", "url", "/message", "url"),
        ("/message/123456", "url", "/message/<message_id>", "url"),
        ("/message-named-route", "endpoint", "hi", "component"),
    ],
)
def test_span_streaming_transaction_style(
    sentry_init,
    capture_items,
    url,
    transaction_style,
    expected_name,
    expected_source,
):
    sentry_init(
        integrations=[BottleIntegration(transaction_style=transaction_style)],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("span")

    app = Bottle()

    @app.route("/message")
    def hi():
        return "ok"

    @app.route("/message/<message_id>")
    def hi_with_id(message_id):
        return "ok"

    @app.route("/message-named-route", name="hi")
    def named_hi():
        return "ok"

    client = Client(app)
    client.get(url)

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    assert len(spans) == 3

    callback_span = spans[0]
    handle_span = spans[1]
    segment = spans[2]

    assert segment["is_segment"] is True
    assert handle_span["parent_span_id"] == segment["span_id"]
    assert callback_span["parent_span_id"] == handle_span["span_id"]

    assert handle_span["name"].endswith(expected_name)
    assert handle_span["attributes"]["sentry.span.source"] == expected_source


def test_span_streaming_with_error(sentry_init, capture_items):
    sentry_init(
        integrations=[BottleIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("event", "span")

    app = Bottle()

    @app.route("/error")
    def error():
        1 / 0

    client = Client(app)
    try:
        client.get("/error")
    except ZeroDivisionError:
        pass

    sentry_sdk.flush()

    events = [item.payload for item in items if item.type == "event"]
    spans = [item.payload for item in items if item.type == "span"]
    assert len(events) == 1
    assert len(spans) == 3

    error_event = events[0]
    callback_span = spans[0]
    handle_span = spans[1]
    segment = spans[2]

    # All share the same trace
    assert callback_span["trace_id"] == error_event["contexts"]["trace"]["trace_id"]
    assert handle_span["trace_id"] == error_event["contexts"]["trace"]["trace_id"]
    assert segment["trace_id"] == error_event["contexts"]["trace"]["trace_id"]

    # Span hierarchy
    assert segment["is_segment"] is True
    assert "parent_span_id" not in segment
    assert handle_span["parent_span_id"] == segment["span_id"]
    assert callback_span["parent_span_id"] == handle_span["span_id"]

    # Error event span_id points to the callback span (where the exception was raised)
    assert error_event["contexts"]["trace"]["span_id"] == callback_span["span_id"]

    # Span statuses
    assert segment["status"] == "error"
    assert callback_span["status"] == "error"

    # Bottle mechanism on the error event
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "bottle"
    assert error_event["exception"]["values"][0]["mechanism"]["handled"] is False


@pytest.mark.parametrize(
    "status_code,expected_span_status",
    [
        (200, "ok"),
        (404, "error"),
        (500, "error"),
    ],
)
def test_span_streaming_http_error_status(
    sentry_init,
    capture_items,
    status_code,
    expected_span_status,
):
    sentry_init(
        integrations=[BottleIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("span")

    app = Bottle()

    @app.route("/")
    def handle():
        return HTTPResponse(status=status_code, body="response")

    client = Client(app)
    client.get("/")

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    assert len(spans) == 3

    callback_span = spans[0]
    handle_span = spans[1]
    segment = spans[2]

    assert segment["is_segment"] is True
    assert handle_span["parent_span_id"] == segment["span_id"]
    assert callback_span["parent_span_id"] == handle_span["span_id"]

    assert segment["status"] == expected_span_status
    assert segment["attributes"]["http.response.status_code"] == status_code


@pytest.mark.parametrize("raise_error", [True, False])
@pytest.mark.parametrize(
    ("integration_kwargs", "status_code", "should_capture"),
    (
        ({}, 500, True),
        ({}, 400, False),
        ({"failed_request_status_codes": set()}, 500, False),
        ({"failed_request_status_codes": {404, *range(500, 600)}}, 404, True),
        ({"failed_request_status_codes": {404, *range(500, 600)}}, 400, False),
    ),
)
def test_span_streaming_failed_request_status_codes(
    sentry_init,
    capture_items,
    integration_kwargs,
    status_code,
    should_capture,
    raise_error,
):
    sentry_init(
        integrations=[BottleIntegration(**integration_kwargs)],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("event", "span")

    app = Bottle()

    @app.route("/")
    def handle():
        response = HTTPResponse(status=status_code)
        if raise_error:
            raise response
        return response

    client = Client(app, Response)
    client.get("/")

    sentry_sdk.flush()

    events = [item.payload for item in items if item.type == "event"]
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 3

    callback_span = spans[0]
    handle_span = spans[1]
    segment = spans[2]

    assert segment["is_segment"] is True
    assert handle_span["parent_span_id"] == segment["span_id"]
    assert callback_span["parent_span_id"] == handle_span["span_id"]

    if should_capture:
        assert len(events) == 1
        assert events[0]["exception"]["values"][0]["type"] == "HTTPResponse"
        assert events[0]["exception"]["values"][0]["mechanism"]["handled"] is True
    else:
        assert len(events) == 0

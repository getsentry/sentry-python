import json

from io import BytesIO

import logging

import pytest

from pyramid.authorization import ACLAuthorizationPolicy
import pyramid.testing

from pyramid.response import Response

from werkzeug.test import Client

from sentry_sdk import capture_message
from sentry_sdk.integrations.pyramid import PyramidIntegration


def hi(request):
    capture_message("hi")
    return Response("hi")


@pytest.fixture
def pyramid_config():
    config = pyramid.testing.setUp()
    try:
        config.add_route("hi", "/message")
        config.add_view(hi, route_name="hi")
        yield config
    finally:
        pyramid.testing.tearDown()


@pytest.fixture
def route(pyramid_config):
    def inner(url):
        def wrapper(f):
            pyramid_config.add_route(f.__name__, url)
            pyramid_config.add_view(f, route_name=f.__name__)
            return f

        return wrapper

    return inner


@pytest.fixture
def get_client(pyramid_config):
    def inner():
        return Client(pyramid_config.make_wsgi_app())

    return inner


def test_view_exceptions(
    get_client, route, sentry_init, capture_events, capture_exceptions
):
    sentry_init(integrations=[PyramidIntegration()])
    events = capture_events()
    exceptions = capture_exceptions()

    @route("/errors")
    def errors(request):
        1 / 0

    client = get_client()
    with pytest.raises(ZeroDivisionError):
        client.get("/errors")

    error, = exceptions
    assert isinstance(error, ZeroDivisionError)

    event, = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "pyramid"


def test_has_context(route, get_client, sentry_init, capture_events):
    sentry_init(integrations=[PyramidIntegration()])
    events = capture_events()

    @route("/message/{msg}")
    def hi2(request):
        capture_message(request.matchdict["msg"])
        return Response("hi")

    client = get_client()
    client.get("/message/yoo")

    event, = events
    assert event["message"] == "yoo"
    assert event["request"] == {
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Content-Length": "0", "Content-Type": "", "Host": "localhost"},
        "method": "GET",
        "query_string": "",
        "url": "http://localhost/message/yoo",
    }
    assert event["transaction"] == "hi2"


@pytest.mark.parametrize(
    "transaction_style,expected_transaction",
    [("route_name", "hi"), ("route_pattern", "/message")],
)
def test_transaction_style(
    sentry_init, get_client, capture_events, transaction_style, expected_transaction
):
    sentry_init(integrations=[PyramidIntegration(transaction_style=transaction_style)])

    events = capture_events()
    client = get_client()
    client.get("/message")

    event, = events
    assert event["transaction"] == expected_transaction


def test_large_json_request(sentry_init, capture_events, route, get_client):
    sentry_init(integrations=[PyramidIntegration()])

    data = {"foo": {"bar": "a" * 2000}}

    @route("/")
    def index(request):
        assert request.json == data
        assert request.text == json.dumps(data)
        assert not request.POST
        capture_message("hi")
        return Response("ok")

    events = capture_events()

    client = get_client()
    client.post("/", content_type="application/json", data=json.dumps(data))

    event, = events
    assert event["_meta"]["request"]["data"]["foo"]["bar"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == 512


def test_files_and_form(sentry_init, capture_events, route, get_client):
    sentry_init(integrations=[PyramidIntegration()], request_bodies="always")

    data = {"foo": "a" * 2000, "file": (BytesIO(b"hello"), "hello.txt")}

    @route("/")
    def index(request):
        capture_message("hi")
        return Response("ok")

    events = capture_events()

    client = get_client()
    client.post("/", data=data)

    event, = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]) == 512

    assert event["_meta"]["request"]["data"]["file"] == {
        "": {"len": 0, "rem": [["!raw", "x", 0, 0]]}
    }
    assert not event["request"]["data"]["file"]


def test_bad_request_not_captured(
    sentry_init, pyramid_config, capture_events, route, get_client
):
    import pyramid.httpexceptions as exc

    sentry_init(integrations=[PyramidIntegration()])
    events = capture_events()

    @route("/")
    def index(request):
        raise exc.HTTPBadRequest()

    def errorhandler(exc, request):
        return Response("bad request")

    pyramid_config.add_view(errorhandler, context=exc.HTTPBadRequest)

    client = get_client()
    client.get("/")

    assert not events


def test_errorhandler(
    sentry_init, pyramid_config, capture_exceptions, route, get_client
):
    sentry_init(integrations=[PyramidIntegration()])
    errors = capture_exceptions()

    @route("/")
    def index(request):
        raise Exception()

    def errorhandler(exc, request):
        return Response("bad request")

    pyramid_config.add_view(errorhandler, context=Exception)

    client = get_client()
    client.get("/")

    error, = errors
    assert type(error) is Exception


def test_error_in_errorhandler(
    sentry_init, pyramid_config, capture_events, route, get_client
):
    sentry_init(integrations=[PyramidIntegration()])

    @route("/")
    def index(request):
        raise ValueError()

    def error_handler(err, request):
        1 / 0

    pyramid_config.add_view(error_handler, context=ValueError)

    events = capture_events()

    client = get_client()

    with pytest.raises(ZeroDivisionError):
        client.get("/")

    event1, event2 = events

    exception, = event1["exception"]["values"]
    assert exception["type"] == "ValueError"

    exception = event2["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_error_in_authenticated_userid(
    sentry_init, pyramid_config, capture_events, route, get_client
):
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_init(
        send_default_pii=True,
        integrations=[
            PyramidIntegration(),
            LoggingIntegration(event_level=logging.ERROR),
        ],
    )
    logger = logging.getLogger("test_pyramid")

    class AuthenticationPolicy(object):
        def authenticated_userid(self, request):
            logger.error("failed to identify user")

    pyramid_config.set_authorization_policy(ACLAuthorizationPolicy())
    pyramid_config.set_authentication_policy(AuthenticationPolicy())

    events = capture_events()

    client = get_client()
    client.get("/message")

    assert len(events) == 1

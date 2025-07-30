import json
import logging
from io import BytesIO

import pyramid.testing
import pytest
from pyramid.authorization import ACLAuthorizationPolicy
from pyramid.response import Response
from werkzeug.test import Client

from sentry_sdk import capture_message, add_breadcrumb
from sentry_sdk.consts import DEFAULT_MAX_VALUE_LENGTH
from sentry_sdk.integrations.pyramid import PyramidIntegration
from tests.conftest import unpack_werkzeug_response


try:
    from importlib.metadata import version

    PYRAMID_VERSION = tuple(map(int, version("pyramid").split(".")))

except ImportError:
    # < py3.8
    import pkg_resources

    PYRAMID_VERSION = tuple(
        map(int, pkg_resources.get_distribution("pyramid").version.split("."))
    )


def hi(request):
    capture_message("hi")
    return Response("hi")


def hi_with_id(request):
    capture_message("hi with id")
    return Response("hi with id")


@pytest.fixture
def pyramid_config():
    config = pyramid.testing.setUp()
    try:
        config.add_route("hi", "/message")
        config.add_view(hi, route_name="hi")
        config.add_route("hi_with_id", "/message/{message_id}")
        config.add_view(hi_with_id, route_name="hi_with_id")
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

    add_breadcrumb({"message": "hi"})

    @route("/errors")
    def errors(request):
        add_breadcrumb({"message": "hi2"})
        1 / 0

    client = get_client()
    with pytest.raises(ZeroDivisionError):
        client.get("/errors")

    (error,) = exceptions
    assert isinstance(error, ZeroDivisionError)

    (event,) = events
    (breadcrumb,) = event["breadcrumbs"]["values"]
    assert breadcrumb["message"] == "hi2"
    # Checking only the last value in the exceptions list,
    # because Pyramid >= 1.9 returns a chained exception and before just a single exception
    assert event["exception"]["values"][-1]["mechanism"]["type"] == "pyramid"
    assert event["exception"]["values"][-1]["type"] == "ZeroDivisionError"


def test_has_context(route, get_client, sentry_init, capture_events):
    sentry_init(integrations=[PyramidIntegration()])
    events = capture_events()

    @route("/context_message/{msg}")
    def hi2(request):
        capture_message(request.matchdict["msg"])
        return Response("hi")

    client = get_client()
    client.get("/context_message/yoo")

    (event,) = events
    assert event["message"] == "yoo"
    assert event["request"] == {
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Host": "localhost"},
        "method": "GET",
        "query_string": "",
        "url": "http://localhost/context_message/yoo",
    }
    assert event["transaction"] == "hi2"


@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        ("/message", "route_name", "hi", "component"),
        ("/message", "route_pattern", "/message", "route"),
        ("/message/123456", "route_name", "hi_with_id", "component"),
        ("/message/123456", "route_pattern", "/message/{message_id}", "route"),
    ],
)
def test_transaction_style(
    sentry_init,
    get_client,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    sentry_init(integrations=[PyramidIntegration(transaction_style=transaction_style)])

    events = capture_events()
    client = get_client()
    client.get(url)

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


def test_large_json_request(sentry_init, capture_events, route, get_client):
    sentry_init(integrations=[PyramidIntegration()])

    data = {"foo": {"bar": "a" * (DEFAULT_MAX_VALUE_LENGTH + 10)}}

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

    (event,) = events
    assert event["_meta"]["request"]["data"]["foo"]["bar"] == {
        "": {
            "len": DEFAULT_MAX_VALUE_LENGTH + 10,
            "rem": [
                ["!limit", "x", DEFAULT_MAX_VALUE_LENGTH - 3, DEFAULT_MAX_VALUE_LENGTH]
            ],
        }
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == DEFAULT_MAX_VALUE_LENGTH


@pytest.mark.parametrize("data", [{}, []], ids=["empty-dict", "empty-list"])
def test_flask_empty_json_request(sentry_init, capture_events, route, get_client, data):
    sentry_init(integrations=[PyramidIntegration()])

    @route("/")
    def index(request):
        assert request.json == data
        assert request.text == json.dumps(data)
        assert not request.POST
        capture_message("hi")
        return Response("ok")

    events = capture_events()

    client = get_client()
    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response[1] == "200 OK"

    (event,) = events
    assert event["request"]["data"] == data


def test_json_not_truncated_if_max_request_body_size_is_always(
    sentry_init, capture_events, route, get_client
):
    sentry_init(integrations=[PyramidIntegration()])

    data = {"key{}".format(i): "value{}".format(i) for i in range(10**5)}

    @route("/")
    def index(request):
        assert request.json == data
        assert request.text == json.dumps(data)
        capture_message("hi")
        return Response("ok")

    events = capture_events()

    client = get_client()
    client.post("/", content_type="application/json", data=json.dumps(data))

    (event,) = events
    assert event["request"]["data"] == data


def test_files_and_form(sentry_init, capture_events, route, get_client):
    sentry_init(integrations=[PyramidIntegration()])

    data = {
        "foo": "a" * (DEFAULT_MAX_VALUE_LENGTH + 10),
        "file": (BytesIO(b"hello"), "hello.txt"),
    }

    @route("/")
    def index(request):
        capture_message("hi")
        return Response("ok")

    events = capture_events()

    client = get_client()
    client.post("/", data=data)

    (event,) = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {
            "len": DEFAULT_MAX_VALUE_LENGTH + 10,
            "rem": [
                ["!limit", "x", DEFAULT_MAX_VALUE_LENGTH - 3, DEFAULT_MAX_VALUE_LENGTH]
            ],
        }
    }
    assert len(event["request"]["data"]["foo"]) == DEFAULT_MAX_VALUE_LENGTH

    assert event["_meta"]["request"]["data"]["file"] == {"": {"rem": [["!raw", "x"]]}}
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


def test_errorhandler_ok(
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

    assert not errors


@pytest.mark.skipif(
    PYRAMID_VERSION < (1, 9),
    reason="We don't have the right hooks in older Pyramid versions",
)
def test_errorhandler_500(
    sentry_init, pyramid_config, capture_exceptions, route, get_client
):
    sentry_init(integrations=[PyramidIntegration()])
    errors = capture_exceptions()

    @route("/")
    def index(request):
        1 / 0

    def errorhandler(exc, request):
        return Response("bad request", status=500)

    pyramid_config.add_view(errorhandler, context=Exception)

    client = get_client()
    app_iter, status, headers = unpack_werkzeug_response(client.get("/"))
    assert app_iter == b"bad request"
    assert status.lower() == "500 internal server error"

    (error,) = errors

    assert isinstance(error, ZeroDivisionError)


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

    (event,) = events

    exception = event["exception"]["values"][-1]
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

    class AuthenticationPolicy:
        def authenticated_userid(self, request):
            logger.warning("failed to identify user")

    pyramid_config.set_authorization_policy(ACLAuthorizationPolicy())
    pyramid_config.set_authentication_policy(AuthenticationPolicy())

    events = capture_events()

    client = get_client()
    client.get("/message")

    assert len(events) == 1

    # In `authenticated_userid` there used to be a call to `logging.error`. This would print this error in the
    # event processor of the Pyramid integration and the logging integration would capture this and send it to Sentry.
    # This is not possible anymore, because capturing that error in the logging integration would again run all the
    # event processors (from the global, isolation and current scope) and thus would again run the same pyramid
    # event processor that raised the error in the first place, leading on an infinite loop.
    # This test here is now deactivated and always passes, but it is kept here to document the problem.
    # This change in behavior is also mentioned in the migration documentation for Python SDK 2.0

    # assert "message" not in events[0].keys()


def tween_factory(handler, registry):
    def tween(request):
        try:
            response = handler(request)
        except Exception:
            mroute = request.matched_route
            if mroute and mroute.name in ("index",):
                return Response("bad request", status_code=400)
        return response

    return tween


def test_tween_ok(sentry_init, pyramid_config, capture_exceptions, route, get_client):
    sentry_init(integrations=[PyramidIntegration()])
    errors = capture_exceptions()

    @route("/")
    def index(request):
        raise Exception()

    pyramid_config.add_tween(
        "tests.integrations.pyramid.test_pyramid.tween_factory",
        under=pyramid.tweens.INGRESS,
    )

    client = get_client()
    client.get("/")

    assert not errors


def test_span_origin(sentry_init, capture_events, get_client):
    sentry_init(
        integrations=[PyramidIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = get_client()
    client.get("/message")

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.pyramid"

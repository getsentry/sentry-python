import logging

import falcon
import falcon.testing
import pytest

import sentry_sdk
from sentry_sdk.integrations.falcon import FalconIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.utils import parse_version

try:
    import falcon.asgi
except ImportError:
    pass
else:
    import falcon.inspect  # We only need this module for the ASGI test


FALCON_VERSION = parse_version(falcon.__version__)


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

        class CustomError(Exception):
            pass

        class CustomErrorResource:
            def on_get(self, req, resp):
                raise CustomError()

        def custom_error_handler(*args, **kwargs):
            raise falcon.HTTPError(status=falcon.HTTP_400)

        app = falcon.API()
        app.add_route("/message", MessageResource())
        app.add_route("/message/{message_id:int}", MessageByIdResource())
        app.add_route("/custom-error", CustomErrorResource())

        app.add_error_handler(CustomError, custom_error_handler)

        return app

    return inner


@pytest.fixture
def make_client(make_app):
    def inner():
        app = make_app()
        return falcon.testing.TestClient(app)

    return inner


@pytest.mark.parametrize("span_streaming", [True, False])
def test_has_context(
    sentry_init,
    capture_events,
    capture_items,
    make_client,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    client = make_client()
    if span_streaming:
        items = capture_items("event")

        response = client.simulate_get("/message")
        assert response.status == falcon.HTTP_200

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_transaction_style(
    sentry_init,
    make_client,
    capture_events,
    capture_items,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
    span_streaming,
):
    integration = FalconIntegration(transaction_style=transaction_style)
    sentry_init(
        integrations=[integration],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    client = make_client()
    if span_streaming:
        events = capture_items("event")

        response = client.simulate_get(url)
        assert response.status == falcon.HTTP_200
    else:
        events = capture_events()

        response = client.simulate_get(url)
        assert response.status == falcon.HTTP_200

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


@pytest.mark.parametrize("span_streaming", [True, False])
def test_unhandled_errors(
    sentry_init,
    capture_exceptions,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    class Resource:
        def on_get(self, req, resp):
            1 / 0

    app = falcon.API()
    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    exceptions = capture_exceptions()
    if span_streaming:
        items = capture_items("event")

        try:
            client.simulate_get("/")
        except ZeroDivisionError:
            pass

        (exc,) = exceptions
        assert isinstance(exc, ZeroDivisionError)

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        try:
            client.simulate_get("/")
        except ZeroDivisionError:
            pass

        (exc,) = exceptions
        assert isinstance(exc, ZeroDivisionError)

        (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "falcon"
    assert " by zero" in event["exception"]["values"][0]["value"]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_raised_5xx_errors(
    sentry_init,
    capture_exceptions,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    class Resource:
        def on_get(self, req, resp):
            raise falcon.HTTPError(falcon.HTTP_502)

    app = falcon.API()
    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    exceptions = capture_exceptions()
    if span_streaming:
        items = capture_items("event")

        client.simulate_get("/")

        (exc,) = exceptions
        assert isinstance(exc, falcon.HTTPError)

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        client.simulate_get("/")

        (exc,) = exceptions
        assert isinstance(exc, falcon.HTTPError)

        (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "falcon"
    assert event["exception"]["values"][0]["type"] == "HTTPError"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_raised_4xx_errors(
    sentry_init,
    capture_exceptions,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    class Resource:
        def on_get(self, req, resp):
            raise falcon.HTTPError(falcon.HTTP_400)

    app = falcon.API()
    app.add_route("/", Resource())

    exceptions = capture_exceptions()
    if span_streaming:
        events = capture_items("event")
    else:
        events = capture_events()

    client = falcon.testing.TestClient(app)
    client.simulate_get("/")

    assert len(exceptions) == 0
    assert len(events) == 0


@pytest.mark.parametrize("span_streaming", [True, False])
def test_http_status(
    sentry_init,
    capture_exceptions,
    capture_events,
    capture_items,
    span_streaming,
):
    """
    This just demonstrates, that if Falcon raises a HTTPStatus with code 500
    (instead of a HTTPError with code 500) Sentry will not capture it.
    """
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    class Resource:
        def on_get(self, req, resp):
            raise falcon.http_status.HTTPStatus(falcon.HTTP_508)

    app = falcon.API()
    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    exceptions = capture_exceptions()
    if span_streaming:
        events = capture_items("event")

        client.simulate_get("/")
    else:
        events = capture_events()

        client.simulate_get("/")

    assert len(exceptions) == 0
    assert len(events) == 0


@pytest.mark.parametrize("max_value_length", [1024, None])
@pytest.mark.parametrize("span_streaming", [True, False])
def test_falcon_large_json_request(
    sentry_init,
    capture_events,
    capture_items,
    max_value_length,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        max_request_body_size="always",
        max_value_length=max_value_length,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    data = {"foo": {"bar": "a" * (1034)}}

    class Resource:
        def on_post(self, req, resp):
            assert req.media == data
            sentry_sdk.capture_message("hi")
            resp.media = "ok"

    app = falcon.API()
    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    if span_streaming:
        items = capture_items("event")

        response = client.simulate_post("/", json=data)
        assert response.status == falcon.HTTP_200

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        response = client.simulate_post("/", json=data)
        assert response.status == falcon.HTTP_200

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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_falcon_empty_json_request(
    sentry_init,
    capture_events,
    capture_items,
    data,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    class Resource:
        def on_post(self, req, resp):
            assert req.media == data
            sentry_sdk.capture_message("hi")
            resp.media = "ok"

    app = falcon.API()
    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    if span_streaming:
        items = capture_items("event")

        response = client.simulate_post("/", json=data)
        assert response.status == falcon.HTTP_200

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        response = client.simulate_post("/", json=data)
        assert response.status == falcon.HTTP_200

        (event,) = events
    assert event["request"]["data"] == data


@pytest.mark.parametrize("span_streaming", [True, False])
def test_falcon_raw_data_request(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    class Resource:
        def on_post(self, req, resp):
            sentry_sdk.capture_message("hi")
            resp.media = "ok"

    app = falcon.API()
    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    if span_streaming:
        items = capture_items("event")

        response = client.simulate_post("/", body="hi")
        assert response.status == falcon.HTTP_200

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        response = client.simulate_post("/", body="hi")
        assert response.status == falcon.HTTP_200

        (event,) = events
    assert event["request"]["headers"]["Content-Length"] == "2"
    assert event["request"]["data"] == ""


@pytest.mark.parametrize("span_streaming", [True, False])
def test_logging(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration(), LoggingIntegration(event_level="ERROR")],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    logger = logging.getLogger()

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            logger.error("hi")
            resp.media = "ok"

    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    if span_streaming:
        items = capture_items("event")

        client.simulate_get("/")

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        client.simulate_get("/")

        (event,) = events
    assert event["level"] == "error"


def test_500(sentry_init):
    sentry_init(integrations=[FalconIntegration()])

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            1 / 0

    app.add_route("/", Resource())

    def http500_handler(ex, req, resp, params):
        sentry_sdk.capture_exception(ex)
        resp.media = {"message": "Sentry error."}

    app.add_error_handler(Exception, http500_handler)

    client = falcon.testing.TestClient(app)
    response = client.simulate_get("/")

    assert response.json == {"message": "Sentry error."}


@pytest.mark.parametrize("span_streaming", [True, False])
def test_error_in_errorhandler(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            raise ValueError()

    app.add_route("/", Resource())

    def http500_handler(ex, req, resp, params):
        1 / 0

    app.add_error_handler(Exception, http500_handler)
    client = falcon.testing.TestClient(app)

    if span_streaming:
        items = capture_items("event")

        with pytest.raises(ZeroDivisionError):
            client.simulate_get("/")

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        with pytest.raises(ZeroDivisionError):
            client.simulate_get("/")

        (event,) = events

    last_ex_values = event["exception"]["values"][-1]
    assert last_ex_values["type"] == "ZeroDivisionError"
    assert last_ex_values["stacktrace"]["frames"][-1]["vars"]["ex"] == "ValueError()"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_bad_request_not_captured(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            raise falcon.HTTPBadRequest()

    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    if span_streaming:
        events = capture_items("event")
    else:
        events = capture_events()

    client.simulate_get("/")

    assert not events


@pytest.mark.parametrize("span_streaming", [True, False])
def test_does_not_leak_scope(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    app = falcon.API()

    class Resource:
        def on_get(self, req, resp):
            sentry_sdk.get_isolation_scope().set_tag("request_data", True)

            def generator():
                for row in range(1000):
                    assert sentry_sdk.get_isolation_scope()._tags["request_data"]

                    yield (str(row) + "\n").encode()

            resp.stream = generator()

    app.add_route("/", Resource())

    client = falcon.testing.TestClient(app)

    if span_streaming:
        events = capture_items("event")
    else:
        events = capture_events()

    sentry_sdk.get_isolation_scope().set_tag("request_data", False)

    response = client.simulate_get("/")

    expected_response = "".join(str(row) + "\n" for row in range(1000))
    assert response.text == expected_response
    assert not events
    assert not sentry_sdk.get_isolation_scope()._tags["request_data"]


@pytest.mark.skipif(
    not hasattr(falcon, "asgi"), reason="This Falcon version lacks ASGI support."
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_falcon_not_breaking_asgi(sentry_init, span_streaming):
    """
    This test simply verifies that the Falcon integration does not break ASGI
    Falcon apps.

    The test does not verify ASGI Falcon support, since our Falcon integration
    currently lacks support for ASGI Falcon apps.
    """
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    asgi_app = falcon.asgi.App()

    try:
        falcon.inspect.inspect_app(asgi_app)
    except TypeError:
        pytest.fail("Falcon integration causing errors in ASGI apps.")


@pytest.mark.skipif(
    (FALCON_VERSION or ()) < (3,),
    reason="The Sentry Falcon integration only supports custom error handlers on Falcon 3+",
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_falcon_custom_error_handler(
    sentry_init,
    make_app,
    capture_events,
    capture_items,
    span_streaming,
):
    """
    When a custom error handler handles what otherwise would have resulted in a 5xx error,
    changing the HTTP status to a non-5xx status, no error event should be sent to Sentry.
    """
    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    app = make_app()
    client = falcon.testing.TestClient(app)

    if span_streaming:
        events = capture_items("event")
    else:
        events = capture_events()

    client.simulate_get("/custom-error")

    assert len(events) == 0


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    make_client,
    span_streaming,
):
    sentry_init(
        integrations=[FalconIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    client = make_client()

    if span_streaming:
        items = capture_items("span")

        client.simulate_get("/message")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[0]["attributes"]["sentry.origin"] == "auto.http.falcon"
    else:
        events = capture_events()

        client.simulate_get("/message")

        (_, event) = events

        assert event["contexts"]["trace"]["origin"] == "auto.http.falcon"


@pytest.mark.parametrize("span_streaming", [True, False])
def test_falcon_request_media(sentry_init, span_streaming):
    # test_passed stores whether the test has passed.
    test_passed = False

    # test_failure_reason stores the reason why the test failed
    # if test_passed is False. The value is meaningless when
    # test_passed is True.
    test_failure_reason = "test endpoint did not get called"

    class SentryCaptureMiddleware:
        def process_request(self, _req, _resp):
            # This capture message forces Falcon event processors to run
            # before the request handler runs
            sentry_sdk.capture_message("Processing request")

    class RequestMediaResource:
        def on_post(self, req, _):
            nonlocal test_passed, test_failure_reason
            raw_data = req.bounded_stream.read()

            # If the raw_data is empty, the request body stream
            # has been exhausted by the SDK. Test should fail in
            # this case.
            test_passed = raw_data != b""
            test_failure_reason = "request body has been read"

    sentry_init(
        integrations=[FalconIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    try:
        app_class = falcon.App  # Falcon ≥3.0
    except AttributeError:
        app_class = falcon.API  # Falcon <3.0

    app = app_class(middleware=[SentryCaptureMiddleware()])
    app.add_route("/read_body", RequestMediaResource())

    client = falcon.testing.TestClient(app)

    client.simulate_post("/read_body", json={"foo": "bar"})

    # Check that simulate_post actually calls the resource, and
    # that the SDK does not exhaust the request body stream.
    assert test_passed, test_failure_reason

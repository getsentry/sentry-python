import base64
import functools
import os

import pytest

from sentry_sdk import capture_exception, capture_message, last_event_id
from sentry_sdk.integrations.starlite import StarliteIntegration
from sentry_sdk.utils import AnnotatedValue

starlite = pytest.importorskip("starlite")

from typing import Any, Dict

from starlite import AbstractMiddleware, LoggingConfig, Starlite, get
from starlite.middleware import LoggingMiddlewareConfig, RateLimitConfig
from starlite.middleware.session.memory_backend import MemoryBackendConfig
from starlite.status_codes import HTTP_500_INTERNAL_SERVER_ERROR
from starlite.testing import TestClient

PICTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photo.jpg")

BODY_JSON = {"some": "json", "for": "testing", "nested": {"numbers": 123}}

BODY_FORM = """--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="username"\r\n\r\nJane\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="password"\r\n\r\nhello123\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="photo"; filename="photo.jpg"\r\nContent-Type: image/jpg\r\nContent-Transfer-Encoding: base64\r\n\r\n{{image_data}}\r\n--fd721ef49ea403a6--\r\n""".replace(
    "{{image_data}}", str(base64.b64encode(open(PICTURE, "rb").read()))
)

PARSED_FORM = starlite.datastructures.FormMultiDict(
    [
        ("username", "Jane"),
        ("password", "hello123"),
        (
            "photo",
            starlite.datastructures.UploadFile(
                filename="photo.jpg",
                file=open(PICTURE, "rb"),
                content_type="image/jpeg",
            ),
        ),
    ]
)
PARSED_BODY = {
    "username": "Jane",
    "password": "hello123",
    "photo": AnnotatedValue(
        "", {"len": 28023, "rem": [["!raw", "x", 0, 28023]]}
    ),  # size of photo.jpg read above
}

# Dummy ASGI scope for creating mock Starlette requests
SCOPE = {
    "client": ("172.29.0.10", 34784),
    "headers": [
        [b"host", b"example.com"],
        [b"user-agent", b"Mozilla/5.0 Gecko/20100101 Firefox/60.0"],
        [b"content-type", b"application/json"],
        [b"accept-language", b"en-US,en;q=0.5"],
        [b"accept-encoding", b"gzip, deflate, br"],
        [b"upgrade-insecure-requests", b"1"],
        [b"cookie", b"yummy_cookie=choco; tasty_cookie=strawberry"],
    ],
    "http_version": "0.0",
    "method": "GET",
    "path": "/path",
    "query_string": b"qs=hello",
    "scheme": "http",
    "server": ("172.28.0.10", 8000),
    "type": "http",
}


class SampleMiddleware(AbstractMiddleware):
    async def __call__(self, scope, receive, send) -> None:
        async def do_stuff(message):
            if message["type"] == "http.response.start":
                # do something here.
                pass
            await send(message)

        await self.app(scope, receive, do_stuff)


class SampleReceiveSendMiddleware(AbstractMiddleware):
    async def __call__(self, scope, receive, send):
        message = await receive()
        assert message
        assert message["type"] == "http.request"

        send_output = await send({"type": "something-unimportant"})
        assert send_output is None

        await self.app(scope, receive, send)


class SamplePartialReceiveSendMiddleware(AbstractMiddleware):
    async def __call__(self, scope, receive, send):
        message = await receive()
        assert message
        assert message["type"] == "http.request"

        send_output = await send({"type": "something-unimportant"})
        assert send_output is None

        async def my_receive(*args, **kwargs):
            pass

        async def my_send(*args, **kwargs):
            pass

        partial_receive = functools.partial(my_receive)
        partial_send = functools.partial(my_send)

        await self.app(scope, partial_receive, partial_send)


def starlite_app_factory(middleware=None, debug=True, exception_handlers=None):
    @get("/some_url")
    async def homepage_handler() -> Dict[str, Any]:
        1 / 0
        return {"status": "ok"}

    @get("/custom_error")
    async def custom_error() -> Any:
        raise Exception("Too Hot")

    @get("/message")
    async def message() -> Dict[str, Any]:
        capture_message("hi")
        return {"status": "ok"}

    @get("/message/{message_id:str}")
    async def message_with_id() -> Dict[str, Any]:
        capture_message("hi")
        return {"status": "ok"}

    logging_config = LoggingConfig()

    app = Starlite(
        route_handlers=[homepage_handler, custom_error, message, message_with_id],
        debug=debug,
        middleware=middleware,
        logging_config=logging_config,
        exception_handlers=exception_handlers,
    )

    return app


@pytest.mark.parametrize(
    "test_url,expected_error,expected_message",
    [
        ("/some_url", ZeroDivisionError, "division by zero"),
        ("/custom_error", Exception, "Too Hot"),
    ],
)
def test_catch_exceptions(
    sentry_init,
    capture_exceptions,
    capture_events,
    test_url,
    expected_error,
    expected_message,
):
    sentry_init(integrations=[StarliteIntegration()])
    starlite_app = starlite_app_factory()
    exceptions = capture_exceptions()
    events = capture_events()

    client = TestClient(starlite_app)
    try:
        client.get(test_url)
    except Exception:
        pass

    (exc,) = exceptions
    assert isinstance(exc, expected_error)
    assert str(exc) == expected_message

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "starlite"


def test_middleware_spans(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
    )

    logging_config = LoggingMiddlewareConfig()
    session_config = MemoryBackendConfig()
    rate_limit_config = RateLimitConfig(rate_limit=("hour", 5))

    starlite_app = starlite_app_factory(
        middleware=[
            session_config.middleware,
            logging_config.middleware,
            rate_limit_config.middleware,
        ]
    )
    events = capture_events()

    client = TestClient(
        starlite_app, raise_server_exceptions=False, base_url="http://testserver.local"
    )
    try:
        client.get("/message")
    except Exception:
        pass

    (_, transaction_event) = events

    expected = ["SessionMiddleware", "LoggingMiddleware", "RateLimitMiddleware"]

    idx = 0
    for span in transaction_event["spans"]:
        if span["op"] == "middleware.starlite":
            assert span["description"] == expected[idx]
            assert span["tags"]["starlite.middleware_name"] == expected[idx]
            idx += 1


def test_middleware_callback_spans(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
    )
    starlette_app = starlite_app_factory(middleware=[SampleMiddleware])
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message")
    except Exception:
        pass

    (_, transaction_event) = events

    expected = [
        {
            "op": "middleware.starlite",
            "description": "SampleMiddleware",
            "tags": {"starlite.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.starlite.send",
            "description": "TestClientTransport.create_send.<locals>.send",
            "tags": {"starlite.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.starlite.send",
            "description": "TestClientTransport.create_send.<locals>.send",
            "tags": {"starlite.middleware_name": "SampleMiddleware"},
        },
    ]
    print(transaction_event["spans"])
    idx = 0
    for span in transaction_event["spans"]:
        assert span["op"] == expected[idx]["op"]
        assert span["description"] == expected[idx]["description"]
        assert span["tags"] == expected[idx]["tags"]
        idx += 1


def test_middleware_receive_send(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
    )
    starlette_app = starlite_app_factory(middleware=[SampleReceiveSendMiddleware])

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        # NOTE: the assert statements checking
        # for correct behaviour are in `SampleReceiveSendMiddleware`!
        client.get("/message")
    except Exception:
        pass


def test_middleware_partial_receive_send(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
    )
    starlette_app = starlite_app_factory(
        middleware=[SamplePartialReceiveSendMiddleware]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message")
    except Exception:
        pass

    (_, transaction_event) = events

    expected = [
        {
            "op": "middleware.starlite",
            "description": "SamplePartialReceiveSendMiddleware",
            "tags": {"starlite.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
        {
            "op": "middleware.starlite.receive",
            "description": "TestClientTransport.create_receive.<locals>.receive",
            "tags": {"starlite.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
        {
            "op": "middleware.starlite.send",
            "description": "TestClientTransport.create_send.<locals>.send",
            "tags": {"starlite.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
    ]

    print(transaction_event["spans"])
    idx = 0
    for span in transaction_event["spans"]:
        assert span["op"] == expected[idx]["op"]
        assert span["description"].startswith(expected[idx]["description"])
        assert span["tags"] == expected[idx]["tags"]
        idx += 1


def test_last_event_id(sentry_init, capture_events):
    sentry_init(
        integrations=[StarliteIntegration()],
    )
    events = capture_events()

    def handler(request, exc):
        capture_exception(exc)
        return starlite.response.Response(last_event_id(), status_code=500)

    app = starlite_app_factory(
        debug=False, exception_handlers={HTTP_500_INTERNAL_SERVER_ERROR: handler}
    )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/custom_error")
    assert response.status_code == 500
    print(events)
    event = events[-1]
    assert response.content.strip().decode("ascii") == event["event_id"]
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "Exception"
    assert exception["value"] == "Too Hot"

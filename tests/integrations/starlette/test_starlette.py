import asyncio
import base64
import functools
import json
import logging
import os
import re
import threading
import warnings
from unittest import mock

import pytest

from sentry_sdk import capture_message, get_baggage, get_traceparent
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations.starlette import (
    StarletteIntegration,
    StarletteRequestExtractor,
)
from sentry_sdk.utils import parse_version

import starlette
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.testclient import TestClient

from tests.integrations.conftest import parametrize_test_configurable_status_codes


STARLETTE_VERSION = parse_version(starlette.__version__)

PICTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photo.jpg")

BODY_JSON = {"some": "json", "for": "testing", "nested": {"numbers": 123}}

BODY_FORM = """--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="username"\r\n\r\nJane\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="password"\r\n\r\nhello123\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="photo"; filename="photo.jpg"\r\nContent-Type: image/jpg\r\nContent-Transfer-Encoding: base64\r\n\r\n{{image_data}}\r\n--fd721ef49ea403a6--\r\n""".replace(
    "{{image_data}}", str(base64.b64encode(open(PICTURE, "rb").read()))
)

FORM_RECEIVE_MESSAGES = [
    {"type": "http.request", "body": BODY_FORM.encode("utf-8")},
    {"type": "http.disconnect"},
]

JSON_RECEIVE_MESSAGES = [
    {"type": "http.request", "body": json.dumps(BODY_JSON).encode("utf-8")},
    {"type": "http.disconnect"},
]

PARSED_FORM = starlette.datastructures.FormData(
    [
        ("username", "Jane"),
        ("password", "hello123"),
        (
            "photo",
            starlette.datastructures.UploadFile(
                filename="photo.jpg",
                file=open(PICTURE, "rb"),
            ),
        ),
    ]
)

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


async def _mock_receive(msg):
    return msg


from starlette.templating import Jinja2Templates


def starlette_app_factory(middleware=None, debug=True):
    template_dir = os.path.join(
        os.getcwd(), "tests", "integrations", "starlette", "templates"
    )
    templates = Jinja2Templates(directory=template_dir)

    async def _homepage(request):
        1 / 0
        return starlette.responses.JSONResponse({"status": "ok"})

    async def _custom_error(request):
        raise Exception("Too Hot")

    async def _message(request):
        capture_message("hi")
        return starlette.responses.JSONResponse({"status": "ok"})

    async def _nomessage(request):
        return starlette.responses.JSONResponse({"status": "ok"})

    async def _message_with_id(request):
        capture_message("hi")
        return starlette.responses.JSONResponse({"status": "ok"})

    def _thread_ids_sync(request):
        return starlette.responses.JSONResponse(
            {
                "main": threading.main_thread().ident,
                "active": threading.current_thread().ident,
            }
        )

    async def _thread_ids_async(request):
        return starlette.responses.JSONResponse(
            {
                "main": threading.main_thread().ident,
                "active": threading.current_thread().ident,
            }
        )

    async def _render_template(request):
        capture_message(get_traceparent() + "\n" + get_baggage())

        template_context = {
            "request": request,
            "msg": "Hello Template World!",
        }
        return templates.TemplateResponse("trace_meta.html", template_context)

    all_methods = [
        "CONNECT",
        "DELETE",
        "GET",
        "HEAD",
        "OPTIONS",
        "PATCH",
        "POST",
        "PUT",
        "TRACE",
    ]

    app = starlette.applications.Starlette(
        debug=debug,
        routes=[
            starlette.routing.Route("/some_url", _homepage),
            starlette.routing.Route("/custom_error", _custom_error),
            starlette.routing.Route("/message", _message),
            starlette.routing.Route("/nomessage", _nomessage, methods=all_methods),
            starlette.routing.Route("/message/{message_id}", _message_with_id),
            starlette.routing.Route("/sync/thread_ids", _thread_ids_sync),
            starlette.routing.Route("/async/thread_ids", _thread_ids_async),
            starlette.routing.Route("/render_template", _render_template),
        ],
        middleware=middleware,
    )

    return app


def async_return(result):
    f = asyncio.Future()
    f.set_result(result)
    return f


class BasicAuthBackend(AuthenticationBackend):
    async def authenticate(self, conn):
        if "Authorization" not in conn.headers:
            return

        auth = conn.headers["Authorization"]
        try:
            scheme, credentials = auth.split()
            if scheme.lower() != "basic":
                return
            decoded = base64.b64decode(credentials).decode("ascii")
        except (ValueError, UnicodeDecodeError):
            raise AuthenticationError("Invalid basic auth credentials")

        username, _, password = decoded.partition(":")

        # TODO: You'd want to verify the username and password here.

        return AuthCredentials(["authenticated"]), SimpleUser(username)


class AsyncIterator:
    def __init__(self, data):
        self.iter = iter(bytes(data, "utf-8"))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return bytes([next(self.iter)])
        except StopIteration:
            raise StopAsyncIteration


class SampleMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # only handle http requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def do_stuff(message):
            if message["type"] == "http.response.start":
                # do something here.
                pass

            await send(message)

        await self.app(scope, receive, do_stuff)


class SampleMiddlewareWithArgs(Middleware):
    def __init__(self, app, bla=None):
        self.app = app
        self.bla = bla


class SampleReceiveSendMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        message = await receive()
        assert message
        assert message["type"] == "http.request"

        send_output = await send({"type": "something-unimportant"})
        assert send_output is None

        await self.app(scope, receive, send)


class SamplePartialReceiveSendMiddleware:
    def __init__(self, app):
        self.app = app

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


@pytest.mark.asyncio
async def test_starletterequestextractor_content_length(sentry_init):
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-length", str(len(json.dumps(BODY_JSON))).encode()],
    ]
    starlette_request = starlette.requests.Request(scope)
    extractor = StarletteRequestExtractor(starlette_request)

    assert await extractor.content_length() == len(json.dumps(BODY_JSON))


@pytest.mark.asyncio
async def test_starletterequestextractor_cookies(sentry_init):
    starlette_request = starlette.requests.Request(SCOPE)
    extractor = StarletteRequestExtractor(starlette_request)

    assert extractor.cookies() == {
        "tasty_cookie": "strawberry",
        "yummy_cookie": "choco",
    }


@pytest.mark.asyncio
async def test_starletterequestextractor_json(sentry_init):
    starlette_request = starlette.requests.Request(SCOPE)

    # Mocking async `_receive()` that works in Python 3.7+
    side_effect = [_mock_receive(msg) for msg in JSON_RECEIVE_MESSAGES]
    starlette_request._receive = mock.Mock(side_effect=side_effect)

    extractor = StarletteRequestExtractor(starlette_request)

    assert extractor.is_json()
    assert await extractor.json() == BODY_JSON


@pytest.mark.asyncio
async def test_starletterequestextractor_form(sentry_init):
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"],
    ]
    # TODO add test for content-type: "application/x-www-form-urlencoded"

    starlette_request = starlette.requests.Request(scope)

    # Mocking async `_receive()` that works in Python 3.7+
    side_effect = [_mock_receive(msg) for msg in FORM_RECEIVE_MESSAGES]
    starlette_request._receive = mock.Mock(side_effect=side_effect)

    extractor = StarletteRequestExtractor(starlette_request)

    form_data = await extractor.form()
    assert form_data.keys() == PARSED_FORM.keys()
    assert form_data["username"] == PARSED_FORM["username"]
    assert form_data["password"] == PARSED_FORM["password"]
    assert form_data["photo"].filename == PARSED_FORM["photo"].filename

    # Make sure we still can read the body
    # after alreading it with extractor.form() above.
    body = await extractor.request.body()
    assert body


@pytest.mark.asyncio
async def test_starletterequestextractor_body_consumed_twice(
    sentry_init, capture_events
):
    """
    Starlette does cache when you read the request data via `request.json()`
    or `request.body()`, but it does NOT when using `request.form()`.
    So we have an edge case when the Sentry Starlette reads the body using `.form()`
    and the user wants to read the body using `.body()`.
    Because the underlying stream can not be consumed twice and is not cached.

    We have fixed this in `StarletteRequestExtractor.form()` by consuming the body
    first with `.body()` (to put it into the `_body` cache and then consume it with `.form()`.

    If this behavior is changed in Starlette and the `request.form()` in Starlette
    is also caching the body, this test will fail.

    See also https://github.com/encode/starlette/discussions/1933
    """
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"],
    ]

    starlette_request = starlette.requests.Request(scope)

    # Mocking async `_receive()` that works in Python 3.7+
    side_effect = [_mock_receive(msg) for msg in FORM_RECEIVE_MESSAGES]
    starlette_request._receive = mock.Mock(side_effect=side_effect)

    extractor = StarletteRequestExtractor(starlette_request)

    await extractor.request.form()

    with pytest.raises(RuntimeError):
        await extractor.request.body()


@pytest.mark.asyncio
async def test_starletterequestextractor_extract_request_info_too_big(sentry_init):
    sentry_init(
        send_default_pii=True,
        integrations=[StarletteIntegration()],
    )
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"],
        [b"content-length", str(len(BODY_FORM)).encode()],
        [b"cookie", b"yummy_cookie=choco; tasty_cookie=strawberry"],
    ]
    starlette_request = starlette.requests.Request(scope)

    # Mocking async `_receive()` that works in Python 3.7+
    side_effect = [_mock_receive(msg) for msg in FORM_RECEIVE_MESSAGES]
    starlette_request._receive = mock.Mock(side_effect=side_effect)

    extractor = StarletteRequestExtractor(starlette_request)

    request_info = await extractor.extract_request_info()

    assert request_info
    assert request_info["cookies"] == {
        "tasty_cookie": "strawberry",
        "yummy_cookie": "choco",
    }
    # Because request is too big only the AnnotatedValue is extracted.
    assert request_info["data"].metadata == {"rem": [["!config", "x"]]}


@pytest.mark.asyncio
async def test_starletterequestextractor_extract_request_info(sentry_init):
    sentry_init(
        send_default_pii=True,
        integrations=[StarletteIntegration()],
    )
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"application/json"],
        [b"content-length", str(len(json.dumps(BODY_JSON))).encode()],
        [b"cookie", b"yummy_cookie=choco; tasty_cookie=strawberry"],
    ]

    starlette_request = starlette.requests.Request(scope)

    # Mocking async `_receive()` that works in Python 3.7+
    side_effect = [_mock_receive(msg) for msg in JSON_RECEIVE_MESSAGES]
    starlette_request._receive = mock.Mock(side_effect=side_effect)

    extractor = StarletteRequestExtractor(starlette_request)

    request_info = await extractor.extract_request_info()

    assert request_info
    assert request_info["cookies"] == {
        "tasty_cookie": "strawberry",
        "yummy_cookie": "choco",
    }
    assert request_info["data"] == BODY_JSON


@pytest.mark.asyncio
async def test_starletterequestextractor_extract_request_info_no_pii(sentry_init):
    sentry_init(
        send_default_pii=False,
        integrations=[StarletteIntegration()],
    )
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"application/json"],
        [b"content-length", str(len(json.dumps(BODY_JSON))).encode()],
        [b"cookie", b"yummy_cookie=choco; tasty_cookie=strawberry"],
    ]

    starlette_request = starlette.requests.Request(scope)

    # Mocking async `_receive()` that works in Python 3.7+
    side_effect = [_mock_receive(msg) for msg in JSON_RECEIVE_MESSAGES]
    starlette_request._receive = mock.Mock(side_effect=side_effect)

    extractor = StarletteRequestExtractor(starlette_request)

    request_info = await extractor.extract_request_info()

    assert request_info
    assert "cookies" not in request_info
    assert request_info["data"] == BODY_JSON


@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        (
            "/message",
            "url",
            "/message",
            "route",
        ),
        (
            "/message",
            "endpoint",
            "tests.integrations.starlette.test_starlette.starlette_app_factory.<locals>._message",
            "component",
        ),
        (
            "/message/123456",
            "url",
            "/message/{message_id}",
            "route",
        ),
        (
            "/message/123456",
            "endpoint",
            "tests.integrations.starlette.test_starlette.starlette_app_factory.<locals>._message_with_id",
            "component",
        ),
    ],
)
def test_transaction_style(
    sentry_init,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    sentry_init(
        integrations=[StarletteIntegration(transaction_style=transaction_style)],
    )
    starlette_app = starlette_app_factory()

    events = capture_events()

    client = TestClient(starlette_app)
    client.get(url)

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


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
    sentry_init(integrations=[StarletteIntegration()])
    starlette_app = starlette_app_factory()
    exceptions = capture_exceptions()
    events = capture_events()

    client = TestClient(starlette_app)
    try:
        client.get(test_url)
    except Exception:
        pass

    (exc,) = exceptions
    assert isinstance(exc, expected_error)
    assert str(exc) == expected_message

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "starlette"


def test_user_information_error(sentry_init, capture_events):
    sentry_init(
        send_default_pii=True,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/custom_error", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (event,) = events
    user = event.get("user", None)
    assert user
    assert "username" in user
    assert user["username"] == "Gabriela"


def test_user_information_error_no_pii(sentry_init, capture_events):
    sentry_init(
        send_default_pii=False,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/custom_error", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (event,) = events
    assert "user" not in event


def test_user_information_transaction(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    client.get("/message", auth=("Gabriela", "hello123"))

    (_, transaction_event) = events
    user = transaction_event.get("user", None)
    assert user
    assert "username" in user
    assert user["username"] == "Gabriela"


def test_user_information_transaction_no_pii(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=False,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    client.get("/message", auth=("Gabriela", "hello123"))

    (_, transaction_event) = events
    assert "user" not in transaction_event


def test_middleware_spans(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (_, transaction_event) = events

    expected_middleware_spans = [
        "ServerErrorMiddleware",
        "AuthenticationMiddleware",
        "ExceptionMiddleware",
        "AuthenticationMiddleware",  # 'op': 'middleware.starlette.send'
        "ServerErrorMiddleware",  # 'op': 'middleware.starlette.send'
        "AuthenticationMiddleware",  # 'op': 'middleware.starlette.send'
        "ServerErrorMiddleware",  # 'op': 'middleware.starlette.send'
    ]

    assert len(transaction_event["spans"]) == len(expected_middleware_spans)

    idx = 0
    for span in transaction_event["spans"]:
        if span["op"].startswith("middleware.starlette"):
            assert (
                span["tags"]["starlette.middleware_name"]
                == expected_middleware_spans[idx]
            )
            idx += 1


def test_middleware_spans_disabled(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration(middleware_spans=False)],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (_, transaction_event) = events

    assert len(transaction_event["spans"]) == 0


def test_middleware_callback_spans(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(middleware=[Middleware(SampleMiddleware)])
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (_, transaction_event) = events

    expected = [
        {
            "op": "middleware.starlette",
            "description": "ServerErrorMiddleware",
            "tags": {"starlette.middleware_name": "ServerErrorMiddleware"},
        },
        {
            "op": "middleware.starlette",
            "description": "SampleMiddleware",
            "tags": {"starlette.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.starlette",
            "description": "ExceptionMiddleware",
            "tags": {"starlette.middleware_name": "ExceptionMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "SampleMiddleware.__call__.<locals>.do_stuff",
            "tags": {"starlette.middleware_name": "ExceptionMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "ServerErrorMiddleware.__call__.<locals>._send",
            "tags": {"starlette.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"starlette.middleware_name": "ServerErrorMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "SampleMiddleware.__call__.<locals>.do_stuff",
            "tags": {"starlette.middleware_name": "ExceptionMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "ServerErrorMiddleware.__call__.<locals>._send",
            "tags": {"starlette.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"starlette.middleware_name": "ServerErrorMiddleware"},
        },
    ]

    idx = 0
    for span in transaction_event["spans"]:
        assert span["op"] == expected[idx]["op"]
        assert span["description"] == expected[idx]["description"]
        assert span["tags"] == expected[idx]["tags"]
        idx += 1


def test_middleware_receive_send(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(SampleReceiveSendMiddleware)]
    )

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        # NOTE: the assert statements checking
        # for correct behaviour are in `SampleReceiveSendMiddleware`!
        client.get("/message", auth=("Gabriela", "hello123"))
    except Exception:
        pass


def test_middleware_partial_receive_send(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration()],
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(SamplePartialReceiveSendMiddleware)]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (_, transaction_event) = events

    expected = [
        {
            "op": "middleware.starlette",
            "description": "ServerErrorMiddleware",
            "tags": {"starlette.middleware_name": "ServerErrorMiddleware"},
        },
        {
            "op": "middleware.starlette",
            "description": "SamplePartialReceiveSendMiddleware",
            "tags": {"starlette.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
        {
            "op": "middleware.starlette.receive",
            "description": (
                "_ASGIAdapter.send.<locals>.receive"
                if STARLETTE_VERSION < (0, 21)
                else "_TestClientTransport.handle_request.<locals>.receive"
            ),
            "tags": {"starlette.middleware_name": "ServerErrorMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "ServerErrorMiddleware.__call__.<locals>._send",
            "tags": {"starlette.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"starlette.middleware_name": "ServerErrorMiddleware"},
        },
        {
            "op": "middleware.starlette",
            "description": "ExceptionMiddleware",
            "tags": {"starlette.middleware_name": "ExceptionMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "functools.partial(<function SamplePartialReceiveSendMiddleware.__call__.<locals>.my_send at ",
            "tags": {"starlette.middleware_name": "ExceptionMiddleware"},
        },
        {
            "op": "middleware.starlette.send",
            "description": "functools.partial(<function SamplePartialReceiveSendMiddleware.__call__.<locals>.my_send at ",
            "tags": {"starlette.middleware_name": "ExceptionMiddleware"},
        },
    ]

    idx = 0
    for span in transaction_event["spans"]:
        assert span["op"] == expected[idx]["op"]
        assert span["description"].startswith(expected[idx]["description"])
        assert span["tags"] == expected[idx]["tags"]
        idx += 1


def test_middleware_args(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration()],
    )
    _ = starlette_app_factory(
        middleware=[Middleware(SampleMiddlewareWithArgs, "bla")]
    )

    # Only creating the App with an Middleware with args
    # should not raise an error
    # So as long as test passes, we are good


def test_legacy_setup(
    sentry_init,
    capture_events,
):
    # Check that behaviour does not change
    # if the user just adds the new Integration
    # and forgets to remove SentryAsgiMiddleware
    sentry_init()
    app = starlette_app_factory()
    asgi_app = SentryAsgiMiddleware(app)

    events = capture_events()

    client = TestClient(asgi_app)
    client.get("/message/123456")

    (event,) = events
    assert event["transaction"] == "/message/{message_id}"


@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
@mock.patch("sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_active_thread_id(sentry_init, capture_envelopes, teardown_profiling, endpoint):
    sentry_init(
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )
    app = starlette_app_factory()
    asgi_app = SentryAsgiMiddleware(app)

    envelopes = capture_envelopes()

    client = TestClient(asgi_app)
    response = client.get(endpoint)
    assert response.status_code == 200

    data = json.loads(response.content)

    envelopes = [envelope for envelope in envelopes]
    assert len(envelopes) == 1

    profiles = [item for item in envelopes[0].items if item.type == "profile"]
    assert len(profiles) == 1

    for item in profiles:
        transactions = item.payload.json["transactions"]
        assert len(transactions) == 1
        assert str(data["active"]) == transactions[0]["active_thread_id"]

    transactions = [item for item in envelopes[0].items if item.type == "transaction"]
    assert len(transactions) == 1

    for item in transactions:
        transaction = item.payload.json
        trace_context = transaction["contexts"]["trace"]
        assert str(data["active"]) == trace_context["data"]["thread.id"]


def test_original_request_not_scrubbed(sentry_init, capture_events):
    sentry_init(integrations=[StarletteIntegration()])

    events = capture_events()

    async def _error(request):
        logging.critical("Oh no!")
        assert request.headers["Authorization"] == "Bearer ohno"
        assert await request.json() == {"password": "ohno"}
        return starlette.responses.JSONResponse({"status": "Oh no!"})

    app = starlette.applications.Starlette(
        routes=[
            starlette.routing.Route("/error", _error, methods=["POST"]),
        ],
    )

    client = TestClient(app)
    client.post(
        "/error",
        json={"password": "ohno"},
        headers={"Authorization": "Bearer ohno"},
    )

    event = events[0]
    assert event["request"]["data"] == {"password": "[Filtered]"}
    assert event["request"]["headers"]["authorization"] == "[Filtered]"


@pytest.mark.skipif(STARLETTE_VERSION < (0, 24), reason="Requires Starlette >= 0.24")
def test_template_tracing_meta(sentry_init, capture_events):
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[StarletteIntegration()],
    )
    events = capture_events()

    app = starlette_app_factory()

    client = TestClient(app)
    response = client.get("/render_template")
    assert response.status_code == 200

    rendered_meta = response.text
    traceparent, baggage = events[0]["message"].split("\n")
    assert traceparent != ""
    assert baggage != ""

    match = re.match(
        r'^<meta name="sentry-trace" content="([^\"]*)"><meta name="baggage" content="([^\"]*)">',
        rendered_meta,
    )
    assert match is not None
    assert match.group(1) == traceparent

    rendered_baggage = match.group(2)
    assert rendered_baggage == baggage


@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "tests.integrations.starlette.test_starlette.starlette_app_factory.<locals>._message_with_id",
            "component",
        ),
        (
            "/message/123456",
            "url",
            "/message/{message_id}",
            "route",
        ),
    ],
)
def test_transaction_name(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
    capture_envelopes,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[StarletteIntegration(transaction_style=transaction_style)],
        traces_sample_rate=1.0,
    )

    envelopes = capture_envelopes()

    app = starlette_app_factory()
    client = TestClient(app)
    client.get(request_url)

    (_, transaction_envelope) = envelopes
    transaction_event = transaction_envelope.get_transaction_event()

    assert transaction_event["transaction"] == expected_transaction_name
    assert (
        transaction_event["transaction_info"]["source"] == expected_transaction_source
    )


@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "http://testserver/message/123456",
            "url",
        ),
        (
            "/message/123456",
            "url",
            "http://testserver/message/123456",
            "url",
        ),
    ],
)
def test_transaction_name_in_traces_sampler(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
):
    """
    Tests that a custom traces_sampler has a meaningful transaction name.
    In this case the URL or endpoint, because we do not have the route yet.
    """

    def dummy_traces_sampler(sampling_context):
        assert (
            sampling_context["transaction_context"]["name"] == expected_transaction_name
        )
        assert (
            sampling_context["transaction_context"]["source"]
            == expected_transaction_source
        )

    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[StarletteIntegration(transaction_style=transaction_style)],
        traces_sampler=dummy_traces_sampler,
        traces_sample_rate=1.0,
    )

    app = starlette_app_factory()
    client = TestClient(app)
    client.get(request_url)


@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "starlette.middleware.trustedhost.TrustedHostMiddleware",
            "component",
        ),
        (
            "/message/123456",
            "url",
            "http://testserver/message/123456",
            "url",
        ),
    ],
)
def test_transaction_name_in_middleware(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
    capture_envelopes,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
        ],
        traces_sample_rate=1.0,
    )

    envelopes = capture_envelopes()

    middleware = [
        Middleware(
            TrustedHostMiddleware,
            allowed_hosts=["example.com", "*.example.com"],
        ),
    ]

    app = starlette_app_factory(middleware=middleware)
    client = TestClient(app)
    client.get(request_url)

    (transaction_envelope,) = envelopes
    transaction_event = transaction_envelope.get_transaction_event()

    assert transaction_event["contexts"]["response"]["status_code"] == 400
    assert transaction_event["transaction"] == expected_transaction_name
    assert (
        transaction_event["transaction_info"]["source"] == expected_transaction_source
    )


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[StarletteIntegration()],
        traces_sample_rate=1.0,
    )
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.starlette"
    for span in event["spans"]:
        assert span["origin"] == "auto.http.starlette"


class NonIterableContainer:
    """Wraps any container and makes it non-iterable.

    Used to test backwards compatibility with our old way of defining failed_request_status_codes, which allowed
    passing in a list of (possibly non-iterable) containers. The Python standard library does not provide any built-in
    non-iterable containers, so we have to define our own.
    """

    def __init__(self, inner):
        self.inner = inner

    def __contains__(self, item):
        return item in self.inner


parametrize_test_configurable_status_codes_deprecated = pytest.mark.parametrize(
    "failed_request_status_codes,status_code,expected_error",
    [
        (None, 500, True),
        (None, 400, False),
        ([500, 501], 500, True),
        ([500, 501], 401, False),
        ([range(400, 499)], 401, True),
        ([range(400, 499)], 500, False),
        ([range(400, 499), range(500, 599)], 300, False),
        ([range(400, 499), range(500, 599)], 403, True),
        ([range(400, 499), range(500, 599)], 503, True),
        ([range(400, 403), 500, 501], 401, True),
        ([range(400, 403), 500, 501], 405, False),
        ([range(400, 403), 500, 501], 501, True),
        ([range(400, 403), 500, 501], 503, False),
        ([], 500, False),
        ([NonIterableContainer(range(500, 600))], 500, True),
        ([NonIterableContainer(range(500, 600))], 404, False),
    ],
)
"""Test cases for configurable status codes (deprecated API).
Also used by the FastAPI tests.
"""


@parametrize_test_configurable_status_codes_deprecated
def test_configurable_status_codes_deprecated(
    sentry_init,
    capture_events,
    failed_request_status_codes,
    status_code,
    expected_error,
):
    with pytest.warns(DeprecationWarning):
        starlette_integration = StarletteIntegration(
            failed_request_status_codes=failed_request_status_codes
        )

    sentry_init(integrations=[starlette_integration])

    events = capture_events()

    async def _error(request):
        raise HTTPException(status_code)

    app = starlette.applications.Starlette(
        routes=[
            starlette.routing.Route("/error", _error, methods=["GET"]),
        ],
    )

    client = TestClient(app)
    client.get("/error")

    if expected_error:
        assert len(events) == 1
    else:
        assert not events


@pytest.mark.skipif(
    STARLETTE_VERSION < (0, 21),
    reason="Requires Starlette >= 0.21, because earlier versions do not support HTTP 'HEAD' requests",
)
def test_transaction_http_method_default(sentry_init, capture_events):
    """
    By default OPTIONS and HEAD requests do not create a transaction.
    """
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(),
        ],
    )
    events = capture_events()

    starlette_app = starlette_app_factory()

    client = TestClient(starlette_app)
    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    assert len(events) == 1

    (event,) = events

    assert event["request"]["method"] == "GET"


@pytest.mark.skipif(
    STARLETTE_VERSION < (0, 21),
    reason="Requires Starlette >= 0.21, because earlier versions do not support HTTP 'HEAD' requests",
)
def test_transaction_http_method_custom(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(
                http_methods_to_capture=(
                    "OPTIONS",
                    "head",
                ),  # capitalization does not matter
            ),
        ],
        debug=True,
    )
    events = capture_events()

    starlette_app = starlette_app_factory()

    client = TestClient(starlette_app)
    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    assert len(events) == 2

    (event1, event2) = events

    assert event1["request"]["method"] == "OPTIONS"
    assert event2["request"]["method"] == "HEAD"


@parametrize_test_configurable_status_codes
def test_configurable_status_codes(
    sentry_init,
    capture_events,
    failed_request_status_codes,
    status_code,
    expected_error,
):
    integration_kwargs = {}
    if failed_request_status_codes is not None:
        integration_kwargs["failed_request_status_codes"] = failed_request_status_codes

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        starlette_integration = StarletteIntegration(**integration_kwargs)

    sentry_init(integrations=[starlette_integration])

    events = capture_events()

    async def _error(_):
        raise HTTPException(status_code)

    app = starlette.applications.Starlette(
        routes=[
            starlette.routing.Route("/error", _error, methods=["GET"]),
        ],
    )

    client = TestClient(app)
    client.get("/error")

    assert len(events) == int(expected_error)

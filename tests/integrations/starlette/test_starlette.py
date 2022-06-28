import asyncio
import base64
import json
import logging
import os

import pytest

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

from sentry_sdk.integrations.starlette import (
    StarletteIntegration,
    StarletteRequestExtractor,
)
from sentry_sdk.utils import AnnotatedValue
from sentry_sdk import capture_message

starlette = pytest.importorskip("starlette")
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.testclient import TestClient

PICTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photo.jpg")

BODY_JSON = {"some": "json", "for": "testing", "nested": {"numbers": 123}}

BODY_FORM = """--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="username"\r\n\r\nJane\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="password"\r\n\r\nhello123\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="photo"; filename="photo.jpg"\r\nContent-Type: image/jpg\r\nContent-Transfer-Encoding: base64\r\n\r\n{{image_data}}\r\n--fd721ef49ea403a6--\r\n""".replace(
    "{{image_data}}", str(base64.b64encode(open(PICTURE, "rb").read()))
)

PARSED_FORM = starlette.datastructures.FormData(
    [
        ("username", "Jane"),
        ("password", "hello123"),
        (
            "photo",
            starlette.datastructures.UploadFile(
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


def async_return(result):
    f = asyncio.Future()
    f.set_result(result)
    return f


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


@pytest.mark.asyncio
async def test_starlettrequestextractor_content_length(sentry_init):
    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(json.dumps(BODY_JSON)),
    ):
        starlette_request = starlette.requests.Request(SCOPE)
        extractor = StarletteRequestExtractor(starlette_request)

        assert await extractor.content_length() == len(json.dumps(BODY_JSON))


@pytest.mark.asyncio
async def test_starlettrequestextractor_cookies(sentry_init):
    starlette_request = starlette.requests.Request(SCOPE)
    extractor = StarletteRequestExtractor(starlette_request)

    assert extractor.cookies() == {
        "tasty_cookie": "strawberry",
        "yummy_cookie": "choco",
    }


@pytest.mark.asyncio
async def test_starlettrequestextractor_json(sentry_init):
    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(json.dumps(BODY_JSON)),
    ):
        starlette_request = starlette.requests.Request(SCOPE)
        extractor = StarletteRequestExtractor(starlette_request)

        assert extractor.is_json()
        assert await extractor.json() == BODY_JSON


@pytest.mark.asyncio
async def test_starlettrequestextractor_parsed_body_json(sentry_init):
    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(json.dumps(BODY_JSON)),
    ):
        starlette_request = starlette.requests.Request(SCOPE)
        extractor = StarletteRequestExtractor(starlette_request)

        parsed_body = await extractor.parsed_body()
        assert parsed_body == BODY_JSON


@pytest.mark.asyncio
async def test_starlettrequestextractor_parsed_body_form(sentry_init):
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"],
    ]
    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(BODY_FORM),
    ):
        starlette_request = starlette.requests.Request(scope)
        extractor = StarletteRequestExtractor(starlette_request)

        parsed_body = await extractor.parsed_body()
        assert parsed_body.keys() == PARSED_BODY.keys()
        assert parsed_body["username"] == PARSED_BODY["username"]
        assert parsed_body["password"] == PARSED_BODY["password"]
        assert parsed_body["photo"].metadata == PARSED_BODY["photo"].metadata


@pytest.mark.asyncio
async def test_starlettrequestextractor_form(sentry_init):
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"],
    ]
    # TODO add test for content-type: "application/x-www-form-urlencoded"

    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(BODY_FORM),
    ):
        starlette_request = starlette.requests.Request(scope)
        extractor = StarletteRequestExtractor(starlette_request)

        form_data = await extractor.form()
        assert form_data.keys() == PARSED_FORM.keys()
        assert form_data["username"] == PARSED_FORM["username"]
        assert form_data["password"] == PARSED_FORM["password"]
        assert form_data["photo"].filename == PARSED_FORM["photo"].filename


@pytest.mark.asyncio
async def test_starlettrequestextractor_raw_data(sentry_init):
    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(json.dumps(BODY_JSON)),
    ):
        starlette_request = starlette.requests.Request(SCOPE)
        extractor = StarletteRequestExtractor(starlette_request)

        assert await extractor.raw_data() == bytes(json.dumps(BODY_JSON), "utf-8")


@pytest.mark.asyncio
async def test_starlettrequestextractor_extract_request_info_too_big(sentry_init):
    sentry_init(
        send_default_pii=True,
        integrations=[StarletteIntegration()],
    )
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"],
        [b"cookie", b"yummy_cookie=choco; tasty_cookie=strawberry"],
    ]
    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(BODY_FORM),
    ):
        starlette_request = starlette.requests.Request(scope)
        extractor = StarletteRequestExtractor(starlette_request)

        request_info = await extractor.extract_request_info()

        assert request_info
        assert request_info["cookies"] == {
            "tasty_cookie": "strawberry",
            "yummy_cookie": "choco",
        }
        # Because request is too big only the AnnotatedValue is extracted.
        assert request_info["data"].metadata == {
            "rem": [["!config", "x", 0, 28355]],
            "len": 28355,
        }


@pytest.mark.asyncio
async def test_starlettrequestextractor_extract_request_info(sentry_init):
    sentry_init(
        send_default_pii=True,
        integrations=[StarletteIntegration()],
    )
    scope = SCOPE.copy()
    scope["headers"] = [
        [b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"],
        [b"cookie", b"yummy_cookie=choco; tasty_cookie=strawberry"],
    ]

    with mock.patch(
        "starlette.requests.Request.stream",
        return_value=AsyncIterator(json.dumps(BODY_JSON)),
    ):
        starlette_request = starlette.requests.Request(scope)
        extractor = StarletteRequestExtractor(starlette_request)

        request_info = await extractor.extract_request_info()

        assert request_info
        assert request_info["cookies"] == {
            "tasty_cookie": "strawberry",
            "yummy_cookie": "choco",
        }
        assert request_info["data"] == BODY_JSON


def starlette_app_factory(middleware=[]):
    async def _homepage(request):
        1 / 0
        return starlette.responses.JSONResponse({"status": "ok"})

    async def _other_endpoint(request):
        try:
            raise ValueError("stuff")
        except Exception:
            logging.exception("stuff happened")
        1 / 0

    async def _custom_error(request):
        raise Exception("Too Hot")

    async def _message(request):
        capture_message("hi")
        return starlette.responses.JSONResponse({"status": "ok"})

    app = starlette.applications.Starlette(
        debug=True,
        routes=[
            starlette.routing.Route("/some_url", _homepage),
            starlette.routing.Route("/other_url", _other_endpoint),
            starlette.routing.Route("/custom_error", _custom_error),
            starlette.routing.Route("/message", _message),
        ],
        middleware=middleware,
    )

    return app


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


@pytest.mark.parametrize(
    "transaction_style,expected_transaction",
    [
        ("url", "/some_url"),
        ("endpoint", "_homepage"),
    ],
)
def test_transaction_naming(
    sentry_init, capture_events, transaction_style, expected_transaction
):
    sentry_init(
        integrations=[StarletteIntegration(transaction_style=transaction_style)]
    )
    starlette_app = starlette_app_factory()
    events = capture_events()

    client = TestClient(starlette_app)
    try:
        client.get("/some_url")
    except ZeroDivisionError:
        pass

    (event,) = events
    assert event["transaction"] == expected_transaction


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


def test_user_information(sentry_init, capture_events, capture_envelopes):
    sentry_init(integrations=[StarletteIntegration()])
    starlette_app = starlette_app_factory(
        middleware=[Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())]
    )
    events = capture_events()
    envelopes = capture_envelopes()

    client = TestClient(starlette_app, raise_server_exceptions=False)
    try:
        client.get("/message", auth=("Gabriela", "hello123"))
    except Exception:
        pass

    (first_event,) = events
    assert first_event.get("user", None)

    (first_envelope, second_envelope) = envelopes
    assert 1 == 0


# Test Middleware Spans for cool waterfall charts in performance

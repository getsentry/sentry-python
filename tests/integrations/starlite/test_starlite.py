import base64
import os

import pytest
from sentry_sdk import capture_message
from sentry_sdk.utils import AnnotatedValue

starlite = pytest.importorskip("starlite")
_ = pytest.skip("typing")

from typing import Dict, Any
from starlite import Starlite, get

PICTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photo.jpg")

BODY_JSON = {"some": "json", "for": "testing", "nested": {"numbers": 123}}

BODY_FORM = """--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="username"\r\n\r\nJane\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="password"\r\n\r\nhello123\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="photo"; filename="photo.jpg"\r\nContent-Type: image/jpg\r\nContent-Transfer-Encoding: base64\r\n\r\n{{image_data}}\r\n--fd721ef49ea403a6--\r\n""".replace(
    "{{image_data}}", str(base64.b64encode(open(PICTURE, "rb").read()))
)

PARSED_FORM = starlite.datastructures.FormData(
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


def starlette_app_factory(middleware=None, debug=True):
    @get("/some_url")
    async def homepage_handler() -> Dict[str, Any]:
        return {"status": "ok"}

    @get("/custom_error")
    async def custom_error() -> Any:
        raise Exception("Too Hot")

    @get("/message")
    async def message() -> Dict[str, Any]:
        capture_message("hi")
        return {"status": "ok"}

    @get("/message/{message_id}")
    async def message_with_id() -> Dict[str, Any]:
        capture_message("hi")
        return {"status": "ok"}

    app = Starlite(
        route_handlers=[homepage_handler, custom_error, message, message_with_id],
        debug=debug,
        middleware=middleware,
    )

    return app

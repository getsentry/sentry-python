import base64
import json
import logging
import os
import threading
import warnings

import fastapi
import pytest
import starlette
from fastapi import (
    APIRouter,
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.testclient import TestClient

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.feature_flags import add_feature_flag
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.utils import parse_version

FASTAPI_VERSION = parse_version(fastapi.__version__)
STARLETTE_VERSION = parse_version(starlette.__version__)

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
            ),
        ),
    ]
)

from tests.integrations.conftest import parametrize_test_configurable_status_codes


def fastapi_app_factory():
    app = FastAPI()
    mounted_app = FastAPI()

    @app.get("/error")
    async def _error():
        capture_message("Hi")
        1 / 0
        return {"message": "Hi"}

    @app.get("/message")
    async def _message():
        capture_message("Hi")
        return {"message": "Hi"}

    @mounted_app.get("/nomessage")
    @app.delete("/nomessage")
    @app.get("/nomessage")
    @app.head("/nomessage")
    @app.options("/nomessage")
    @app.patch("/nomessage")
    @app.post("/nomessage")
    @app.put("/nomessage")
    @app.trace("/nomessage")
    async def _nomessage():
        return {"message": "nothing here..."}

    @app.get("/message/{message_id}")
    async def _message_with_id(message_id):
        capture_message("Hi")
        return {"message": "Hi"}

    @app.get("/sync/thread_ids")
    def _thread_ids_sync():
        return {
            "main": str(threading.main_thread().ident),
            "active": str(threading.current_thread().ident),
        }

    @app.get("/async/thread_ids")
    async def _thread_ids_async():
        return {
            "main": str(threading.main_thread().ident),
            "active": str(threading.current_thread().ident),
        }

    @app.post("/body/json")
    async def body_json(payload: dict = Body(...)):
        capture_message("hi")
        return {"status": "ok"}

    @app.post("/body/form")
    async def body_form(
        username: str = Form(...),
        password: str = Form(...),
        photo: UploadFile = File(...),
    ):
        capture_message("hi")
        return {"status": "ok"}

    app.mount("/root", mounted_app)

    return app


@pytest.mark.asyncio
async def test_request_info_json_body(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[StarletteIntegration()],
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()
    client = TestClient(app)

    items = capture_items("event", "span")

    client.post(
        "/body/json",
        json=BODY_JSON,
        headers={
            "cookie": "yummy_cookie=choco; tasty_cookie=strawberry",
        },
    )

    (event,) = (item.payload for item in items if item.type == "event")
    assert event["request"]["cookies"] == {
        "tasty_cookie": "strawberry",
        "yummy_cookie": "choco",
    }
    assert event["request"]["data"] == BODY_JSON

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    server_span = next(
        span for span in spans if span["attributes"]["sentry.op"] == "http.server"
    )

    assert json.loads(server_span["attributes"][SPANDATA.HTTP_REQUEST_BODY_DATA]) == {
        "some": "json",
        "for": "testing",
        "nested": {"numbers": 123},
    }


@pytest.mark.asyncio
async def test_formdata_request_body(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        max_request_body_size="always",
        integrations=[StarletteIntegration()],
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()
    client = TestClient(app)

    items = capture_items("event", "span")

    client.post(
        "/body/form",
        data=BODY_FORM.encode("utf-8"),
        headers={
            "content-type": "multipart/form-data; boundary=fd721ef49ea403a6",
        },
    )

    (event,) = (item.payload for item in items if item.type == "event")
    assert event["request"]["data"].keys() == PARSED_FORM.keys()
    assert event["request"]["data"]["username"] == PARSED_FORM["username"]
    assert event["request"]["data"]["password"] == "[Filtered]"
    assert event["request"]["data"]["photo"] == ""

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    server_span = next(
        span for span in spans if span["attributes"]["sentry.op"] == "http.server"
    )

    # Going forward, the sanitization of data will need to happen within the `before_send_span` hooks
    # See https://sentry.slack.com/archives/C09RR0KD2N7/p1776951331206129?thread_ts=1776951227.440659&cid=C09RR0KD2N7
    parsed_form_attribute = json.loads(
        server_span["attributes"][SPANDATA.HTTP_REQUEST_BODY_DATA]
    )
    assert parsed_form_attribute.keys() == PARSED_FORM.keys()
    assert parsed_form_attribute["username"] == PARSED_FORM["username"]
    assert parsed_form_attribute["password"] == "hello123"
    assert parsed_form_attribute["photo"] == "[Unparsable]"


@pytest.mark.asyncio
async def test_request_body_too_big(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[StarletteIntegration()],
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()
    client = TestClient(app)

    items = capture_items("event", "span")

    client.post(
        "/body/form",
        data=BODY_FORM.encode("utf-8"),
        headers={
            "content-type": "multipart/form-data; boundary=fd721ef49ea403a6",
            "cookie": "yummy_cookie=choco; tasty_cookie=strawberry",
        },
    )

    (event,) = (item.payload for item in items if item.type == "event")
    assert event["request"]["cookies"] == {
        "tasty_cookie": "strawberry",
        "yummy_cookie": "choco",
    }
    # Because request is too big only the AnnotatedValue is extracted.
    assert event["_meta"]["request"]["data"] == {"": {"rem": [["!config", "x"]]}}

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    server_span = next(
        span for span in spans if span["attributes"]["sentry.op"] == "http.server"
    )

    # Because request is too big only the AnnotatedValue is extracted.
    assert (
        server_span["attributes"][SPANDATA.HTTP_REQUEST_BODY_DATA]
        == "[Exceeds maximum size]"
    )


@pytest.mark.asyncio
async def test_formdata_request_body_data_collection_http_bodies_empty(
    sentry_init, capture_items
):
    sentry_init(
        traces_sample_rate=1.0,
        max_request_body_size="always",
        integrations=[StarletteIntegration()],
        trace_lifecycle="stream",
        _experiments={"data_collection": {"http_bodies": []}},
    )

    app = fastapi_app_factory()
    client = TestClient(app)

    headers = {"content-type": "multipart/form-data; boundary=fd721ef49ea403a6"}

    items = capture_items("event", "span")

    client.post("/body/form", data=BODY_FORM.encode("utf-8"), headers=headers)

    (event,) = (item.payload for item in items if item.type == "event")
    assert "data" not in event["request"]

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    server_span = next(
        span for span in spans if span["attributes"]["sentry.op"] == "http.server"
    )
    assert SPANDATA.HTTP_REQUEST_BODY_DATA not in server_span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection, expect_body",
    [
        pytest.param(None, True, id="no_data_collection_experiment"),
        pytest.param({}, True, id="data_collection_http_bodies_default"),
        pytest.param(
            {"http_bodies": ["incoming_request"]},
            True,
            id="data_collection_http_bodies_incoming_request",
        ),
        pytest.param(
            {"http_bodies": []}, False, id="data_collection_http_bodies_empty"
        ),
    ],
)
async def test_request_body_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    expect_body,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration()],
        trace_lifecycle="stream",
        _experiments=(
            {} if data_collection is None else {"data_collection": data_collection}
        ),
    )

    app = fastapi_app_factory()
    client = TestClient(app)

    items = capture_items("event", "span")

    client.post("/body/json", json=BODY_JSON)

    (event,) = (item.payload for item in items if item.type == "event")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    server_span = next(
        span for span in spans if span["attributes"]["sentry.op"] == "http.server"
    )

    if expect_body:
        assert event["request"]["data"] == BODY_JSON
        assert (
            json.loads(server_span["attributes"][SPANDATA.HTTP_REQUEST_BODY_DATA])
            == BODY_JSON
        )
    else:
        assert "data" not in event["request"]
        assert SPANDATA.HTTP_REQUEST_BODY_DATA not in server_span["attributes"]


@pytest.mark.asyncio
async def test_response(sentry_init, capture_events):
    sentry_init(
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()

    events = capture_events()

    client = TestClient(app)
    response = client.get("/message")

    assert response.json() == {"message": "Hi"}

    assert len(events) == 1

    (message_event,) = events
    assert message_event["message"] == "Hi"
    assert message_event["transaction"] == "/message"


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
            "tests.integrations.fastapi.test_fastapi.fastapi_app_factory.<locals>._message",
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
            "tests.integrations.fastapi.test_fastapi.fastapi_app_factory.<locals>._message_with_id",
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
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
            FastApiIntegration(transaction_style=transaction_style),
        ],
    )
    app = fastapi_app_factory()

    events = capture_events()

    client = TestClient(app)
    client.get(url)

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    (event,) = events

    assert "request" not in event
    assert "transaction" not in event


def test_legacy_setup(
    sentry_init,
    capture_events,
):
    # Check that behaviour does not change
    # if the user just adds the new Integrations
    # and forgets to remove SentryAsgiMiddleware
    sentry_init()
    app = fastapi_app_factory()
    asgi_app = SentryAsgiMiddleware(app)

    events = capture_events()

    client = TestClient(asgi_app)
    client.get("/message/123456")

    (event,) = events
    assert event["transaction"] == "/message/{message_id}"


@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
def test_active_thread_id(sentry_init, capture_items, endpoint):
    sentry_init(
        auto_enabling_integrations=False,  # Ensure httpx is not auto-enabled; its legacy start_span interferes with streaming mode
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    app = fastapi_app_factory()

    items = capture_items("span")

    client = TestClient(app)
    response = client.get(endpoint)
    assert response.status_code == 200

    data = json.loads(response.content)

    sentry_sdk.flush()

    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    assert str(data["active"]) == segments[0]["attributes"]["thread.id"]


@pytest.mark.asyncio
async def test_original_request_not_scrubbed(sentry_init, capture_events):
    sentry_init(
        auto_enabling_integrations=False,  # Ensure httpx is not auto-enabled; its legacy start_span interferes with streaming mode
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            LoggingIntegration(event_level=logging.ERROR),
        ],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    app = FastAPI()

    @app.post("/error")
    async def _error(request: Request):
        logging.critical("Oh no!")
        assert request.headers["Authorization"] == "Bearer ohno"
        assert request.headers["Proxy-Authorization"] == "Basic ohno"
        assert await request.json() == {"password": "secret"}

        return {"error": "Oh no!"}

    events = capture_events()

    client = TestClient(app)
    client.post(
        "/error",
        json={"password": "secret"},
        headers={
            "Authorization": "Bearer ohno",
            "Proxy-Authorization": "Basic ohno",
        },
    )

    event = events[0]
    assert event["request"]["data"] == {"password": "[Filtered]"}
    assert event["request"]["headers"]["authorization"] == "[Filtered]"
    assert event["request"]["headers"]["proxy-authorization"] == "[Filtered]"


@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "tests.integrations.fastapi.test_fastapi.fastapi_app_factory.<locals>._message_with_id",
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
    capture_items,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
            FastApiIntegration(transaction_style=transaction_style),
        ],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    app = fastapi_app_factory()

    client = TestClient(app)
    client.get(request_url)

    sentry_sdk.flush()
    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    segment = segments[0]
    assert segment["name"] == expected_transaction_name
    assert (
        segment["attributes"]["sentry.segment.name.source"]
        == expected_transaction_source
    )


def test_transaction_name_with_prefix(
    sentry_init,
    capture_items,
):
    sentry_init(
        auto_enabling_integrations=False,
        integrations=[
            StarletteIntegration(transaction_style="url"),
            FastApiIntegration(transaction_style="url"),
        ],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    app = FastAPI()
    router = APIRouter()

    @router.get("/users/{user_id}")
    async def get_user(user_id: int):
        return {"user_id": user_id}

    app.include_router(router, prefix="/api")

    client = TestClient(app)
    client.get("/api/users/123")

    sentry_sdk.flush()
    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    segment = segments[0]
    assert segment["name"] == "/api/users/{user_id}"
    assert segment["attributes"]["sentry.segment.name.source"] == "route"


def test_route_endpoint_equal_dependant_call(sentry_init):
    """
    Tests that the route endpoint name is equal to the wrapped dependant call name.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
        ],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()

    for route in app.router.routes:
        if not hasattr(route, "dependant"):
            continue
        assert route.endpoint.__qualname__ == route.dependant.call.__qualname__


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
    Tests that a custom traces_sampler retrieves a meaningful transaction name.
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
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()

    client = TestClient(app)
    client.get(request_url)


@pytest.mark.parametrize("middleware_spans", [False, True])
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
    middleware_spans,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
    capture_items,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        auto_enabling_integrations=False,  # Make sure that httpx integration is not added, because it adds tracing information to the starlette test clients request.
        integrations=[
            StarletteIntegration(
                transaction_style=transaction_style, middleware_spans=middleware_spans
            ),
            FastApiIntegration(
                transaction_style=transaction_style, middleware_spans=middleware_spans
            ),
        ],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    app = fastapi_app_factory()

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "example.com",
        ],
    )

    client = TestClient(app)
    client.get(request_url)

    sentry_sdk.flush()
    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    segment = segments[0]
    assert segment["name"] == expected_transaction_name
    assert (
        segment["attributes"]["sentry.segment.name.source"]
        == expected_transaction_source
    )


@pytest.mark.skipif(
    FASTAPI_VERSION < (0, 80),
    reason="Requires FastAPI >= 0.80, because earlier versions do not support HTTP 'HEAD' requests",
)
def test_transaction_http_method_default(sentry_init, capture_items):
    """
    By default OPTIONS and HEAD requests do not create a span.
    """
    sentry_init(
        auto_enabling_integrations=False,
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
        ],
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()

    items = capture_items("span")

    client = TestClient(app)
    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    sentry_sdk.flush()
    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    assert segments[0]["attributes"]["http.request.method"] == "GET"


@pytest.mark.skipif(
    FASTAPI_VERSION < (0, 80),
    reason="Requires FastAPI >= 0.80, because earlier versions do not support HTTP 'HEAD' requests",
)
def test_transaction_http_method_custom(sentry_init, capture_items):
    sentry_init(
        auto_enabling_integrations=False,
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(
                http_methods_to_capture=(
                    "OPTIONS",
                    "head",
                ),  # capitalization does not matter
            ),
            FastApiIntegration(
                http_methods_to_capture=(
                    "OPTIONS",
                    "head",
                ),  # capitalization does not matter
            ),
        ],
        trace_lifecycle="stream",
    )

    app = fastapi_app_factory()

    items = capture_items("span")

    client = TestClient(app)
    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    sentry_sdk.flush()
    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 2

    assert segments[0]["attributes"]["http.request.method"] == "OPTIONS"
    assert segments[1]["attributes"]["http.request.method"] == "HEAD"


def test_request_url(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[
            StarletteIntegration(),
        ],
        trace_lifecycle="stream",
    )

    starlette_app = fastapi_app_factory()

    client = TestClient(starlette_app)

    items = capture_items("span")

    client.get("/root/nomessage")
    sentry_sdk.flush()
    spans = [item.payload for item in items]

    (server_span,) = (
        span for span in spans if span["attributes"].get("sentry.op") == "http.server"
    )
    assert server_span["attributes"][SPANDATA.URL_FULL] == (
        "http://testserver/root/nomessage"
    )
    assert server_span["attributes"][SPANDATA.URL_PATH] == "/root/nomessage"


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
        fastapi_integration = FastApiIntegration(**integration_kwargs)

    sentry_init(integrations=[starlette_integration, fastapi_integration])

    events = capture_events()

    app = FastAPI()

    @app.get("/error")
    async def _error():
        raise HTTPException(status_code)

    client = TestClient(app)
    client.get("/error")

    assert len(events) == int(expected_error)


@pytest.mark.parametrize("transaction_style", ["endpoint", "url"])
def test_app_host(sentry_init, capture_items, transaction_style):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            StarletteIntegration(transaction_style=transaction_style),
            FastApiIntegration(transaction_style=transaction_style),
        ],
        trace_lifecycle="stream",
    )

    app = FastAPI()
    subapp = FastAPI()

    @subapp.get("/subapp")
    async def subapp_route():
        return {"message": "Hello world!"}

    app.host("subapp", subapp)

    items = capture_items("span")

    client = TestClient(app)
    client.get("/subapp", headers={"Host": "subapp"})

    sentry_sdk.flush()
    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    segment = segments[0]

    if transaction_style == "url":
        assert segment["name"] == "/subapp"
    else:
        assert segment["name"].endswith("subapp_route")


@pytest.mark.asyncio
async def test_feature_flags(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        trace_lifecycle="stream",
    )

    events = capture_events()

    app = FastAPI()

    @app.get("/error")
    async def _error():
        add_feature_flag("hello", False)

        with sentry_sdk.traces.start_span(name="test-span"):
            with sentry_sdk.traces.start_span(name="test-span-2"):
                raise ValueError("something is wrong!")

    try:
        client = TestClient(app)
        client.get("/error")
    except ValueError:
        pass

    found = False
    for event in events:
        if "exception" in event.keys():
            assert event["contexts"]["flags"] == {
                "values": [
                    {"flag": "hello", "result": False},
                ]
            }
            found = True

    assert found, "No event with exception found"

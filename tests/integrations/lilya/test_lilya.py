import asyncio
import base64
import functools
import json
import threading
from unittest import mock

import pytest
from lilya._internal._exception_handlers import (
    handle_exception as _direct_handle_exception,
)
from lilya.apps import Lilya
from lilya.authentication import AuthCredentials, AuthenticationBackend, BasicUser
from lilya.background import Task
from lilya.exceptions import AuthenticationError, HTTPException
from lilya.middleware import DefineMiddleware
from lilya.middleware.authentication import AuthenticationMiddleware
from lilya.requests import Request
from lilya.responses import JSONResponse, PlainText, Response, StreamingResponse
from lilya.routing import Include, Path, WebSocketPath
from lilya.testclient import TestClient

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations.lilya import LilyaIntegration, _wrap_exception_handler_maps
from tests.conftest import ApproxDict
from tests.integrations.conftest import parametrize_test_configurable_status_codes


def _transaction_from_events(events):
    transactions = [event for event in events if event.get("type") == "transaction"]
    assert len(transactions) == 1
    return transactions[0]


def _message_from_events(events, message="hi"):
    messages = [
        event
        for event in events
        if event.get("message") == message
        or event.get("logentry", {}).get("message") == message
    ]
    assert len(messages) == 1
    return messages[0]


class BasicAuth(AuthenticationBackend):
    async def authenticate(self, conn):
        if "Authorization" not in conn.headers:
            return None

        scheme, credentials = conn.headers["Authorization"].split()
        assert scheme.lower() == "basic"
        username, _, _ = base64.b64decode(credentials).decode("ascii").partition(":")
        user = BasicUser(username)
        user.id = "user-1"
        user.email = "%s@example.com" % username
        return AuthCredentials(["authenticated"]), user


class FailingAuth(AuthenticationBackend):
    async def authenticate(self, conn):
        raise AuthenticationError("authentication failed")


class RichUser:
    unique_identifier = "user-1"
    display_name = "lilya"
    email = "lilya@example.com"

    def asdict(self):
        return {
            "id": "user-1",
            "username": "lilya",
            "email": "lilya@example.com",
            "is_admin": True,
            "token": "secret",
        }


class RichUserAuth(AuthenticationBackend):
    async def authenticate(self, conn):
        return AuthCredentials(["authenticated"]), RichUser()


class SampleMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


class ReceiveSendMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        message = await receive()
        assert message["type"] == "http.request"

        send_output = await send({"type": "something-unimportant"})
        assert send_output is None

        await self.app(scope, receive, send)


class PartialReceiveSendMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        message = await receive()
        assert message["type"] == "http.request"

        send_output = await send({"type": "something-unimportant"})
        assert send_output is None

        async def my_receive(*args, **kwargs):
            return await receive(*args, **kwargs)

        async def my_send(*args, **kwargs):
            return await send(*args, **kwargs)

        await self.app(scope, functools.partial(my_receive), functools.partial(my_send))


class RaisingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            raise HTTPException(status_code=500, detail="middleware error")
        await self.app(scope, receive, send)


def lilya_app_factory(middleware=None, exception_handlers=None, lifespan=None):
    background_calls = []

    async def error():
        1 / 0

    async def message():
        capture_message("hi")
        return {"status": "ok"}

    async def message_with_id(message_id):
        capture_message("hi")
        return {"message_id": message_id}

    def thread_ids_sync():
        return {
            "main": threading.main_thread().ident,
            "active": threading.current_thread().ident,
        }

    async def thread_ids_async():
        return {
            "main": threading.main_thread().ident,
            "active": threading.current_thread().ident,
        }

    async def handled_http_error():
        raise HTTPException(status_code=500, detail="handled")

    async def bad_request():
        raise HTTPException(status_code=400, detail="bad request")

    async def custom_error():
        raise ValueError("custom")

    async def json_body(request):
        data = await request.json()
        assert data == {"username": "Jane", "password": "secret"}
        capture_message("json body")
        return data

    async def json_echo(request):
        await request.json()
        capture_message("json echo")
        return {"status": "ok"}

    async def form_body(request):
        form = await request.form()
        assert form["username"] == "Jane"
        assert form["password"] == "secret"
        capture_message("form body")
        return {"username": form["username"]}

    async def stream_body(request):
        body = b""
        async for chunk in request.stream():
            body += chunk
        return PlainText(body.decode("utf-8"))

    async def streaming_response():
        async def generator():
            yield "hello"
            yield " "
            yield "world"

        return StreamingResponse(generator(), media_type="text/plain")

    async def background_response():
        async def background_task():
            background_calls.append("done")

        return Response("ok", background=Task(background_task))

    async def auth_message(request):
        capture_message("auth message")
        return {"user": request.user.display_name}

    async def websocket_endpoint(websocket):
        await websocket.accept()
        message = await websocket.receive_text()
        await websocket.send_text("echo:%s" % message)
        await websocket.close()

    async def child_message():
        capture_message("hi")
        return {"status": "ok"}

    child = Lilya(routes=[Path("/message/{message_id}", child_message)])

    routes = [
        Path("/some_url", error),
        Path("/message", message),
        Path("/message/{message_id}", message_with_id),
        Path("/sync/thread_ids", thread_ids_sync),
        Path("/async/thread_ids", thread_ids_async),
        Path("/handled-http", handled_http_error),
        Path("/bad-request", bad_request),
        Path("/custom-error", custom_error),
        Path("/body/json", json_body, methods=["POST"]),
        Path("/body/json/echo", json_echo, methods=["POST"]),
        Path("/body/form", form_body, methods=["POST"]),
        Path("/body/stream", stream_body, methods=["POST"]),
        Path("/streaming-response", streaming_response),
        Path("/background", background_response),
        Path("/auth-message", auth_message),
        WebSocketPath("/ws/{room}", websocket_endpoint),
        Include("/root", app=child),
    ]

    app = Lilya(
        routes=routes,
        middleware=middleware or [],
        exception_handlers=exception_handlers,
        lifespan=lifespan,
    )
    app.background_calls = background_calls
    return app


def test_invalid_transaction_style():
    with pytest.raises(ValueError) as exc:
        LilyaIntegration(transaction_style="route")

    assert (
        str(exc.value)
        == "Invalid value for transaction_style: route (must be in ('endpoint', 'url'))"
    )


@pytest.mark.parametrize(
    "transaction_style,expected_source,expected_name",
    [
        ("url", "route", "/message/{message_id}"),
        (
            "endpoint",
            "component",
            "tests.integrations.lilya.test_lilya.lilya_app_factory.<locals>.message_with_id",
        ),
    ],
)
def test_transaction_name_and_source(
    sentry_init,
    capture_events,
    transaction_style,
    expected_source,
    expected_name,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration(transaction_style=transaction_style)],
    )
    events = capture_events()

    client = TestClient(lilya_app_factory())
    response = client.get("/message/123")

    assert response.status_code == 200
    transaction = _transaction_from_events(events)
    assert transaction["transaction"] == expected_name
    assert transaction["transaction_info"] == {"source": expected_source}


def test_mounted_route_transaction_name(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration(transaction_style="url")],
    )
    events = capture_events()

    client = TestClient(lilya_app_factory())
    response = client.get("/root/message/123")

    assert response.status_code == 200
    transaction = _transaction_from_events(events)
    assert transaction["transaction"] == "/root/message/{message_id}"
    assert transaction["transaction_info"] == {"source": "route"}


def test_manual_asgi_middleware_uses_lilya_route_template(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, default_integrations=False)
    events = capture_events()

    app = SentryAsgiMiddleware(lilya_app_factory(), transaction_style="url")
    client = TestClient(app)
    response = client.get("/message/123")

    assert response.status_code == 200
    transaction = _transaction_from_events(events)
    assert transaction["transaction"] == "/message/{message_id}"
    assert transaction["transaction_info"] == {"source": "route"}


@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
@mock.patch("sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_active_thread_id(sentry_init, capture_envelopes, teardown_profiling, endpoint):
    sentry_init(
        integrations=[LilyaIntegration()],
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )
    envelopes = capture_envelopes()

    client = TestClient(lilya_app_factory())
    response = client.get(endpoint)

    assert response.status_code == 200
    data = response.json()

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


@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
def test_active_thread_id_span_streaming(sentry_init, capture_items, endpoint):
    sentry_init(
        auto_enabling_integrations=False,
        integrations=[LilyaIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("span")

    client = TestClient(lilya_app_factory())
    response = client.get(endpoint)

    assert response.status_code == 200
    data = response.json()

    sentry_sdk.flush()

    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    assert str(data["active"]) == segments[0]["attributes"]["thread.id"]


def test_catch_unhandled_exception(sentry_init, capture_events, capture_exceptions):
    sentry_init(integrations=[LilyaIntegration()])
    events = capture_events()
    exceptions = capture_exceptions()

    client = TestClient(lilya_app_factory())
    with pytest.raises(ZeroDivisionError):
        client.get("/some_url")

    (exception,) = exceptions
    assert isinstance(exception, ZeroDivisionError)

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"] == {
        "type": "lilya",
        "handled": False,
    }
    assert event["transaction"] == "/some_url"


@parametrize_test_configurable_status_codes
def test_handled_http_exception_configurable_status_codes(
    sentry_init,
    capture_events,
    failed_request_status_codes,
    status_code,
    expected_error,
):
    async def endpoint():
        raise HTTPException(status_code=status_code, detail="handled")

    sentry_init(
        integrations=[
            LilyaIntegration(failed_request_status_codes=failed_request_status_codes)
        ],
    )
    events = capture_events()

    app = Lilya(routes=[Path("/", endpoint)])
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/")

    assert response.status_code == status_code
    if expected_error:
        (event,) = events
        assert event["exception"]["values"][0]["mechanism"] == {
            "type": "lilya",
            "handled": True,
        }
    else:
        assert events == []


def test_custom_exception_handler_is_preserved(sentry_init, capture_events):
    async def value_error_handler(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=418)

    sentry_init(integrations=[LilyaIntegration()])
    events = capture_events()

    app = lilya_app_factory(exception_handlers={ValueError: value_error_handler})
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/custom-error")

    assert response.status_code == 418
    assert response.json() == {"error": "custom"}
    assert events == []


def test_exception_handler_scope_maps_can_have_single_entry():
    async def value_error_handler(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=418)

    handler_maps = ({ValueError: value_error_handler},)

    _wrap_exception_handler_maps(handler_maps)

    assert handler_maps[0][ValueError].__name__ == "_sentry_lilya_exception_handler"


def test_middleware_exception_handler_is_captured(sentry_init, capture_events):
    sentry_init(integrations=[LilyaIntegration()])
    events = capture_events()

    app = lilya_app_factory(middleware=[DefineMiddleware(RaisingMiddleware)])
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/message")

    assert response.status_code == 500
    (event,) = events
    assert event["exception"]["values"][0]["mechanism"] == {
        "type": "lilya",
        "handled": False,
    }


@pytest.mark.asyncio
async def test_wrapped_status_handler_captures_handled_http_exception(
    sentry_init, capture_events
):
    async def server_error_handler(request, exc):
        return PlainText("handled", status_code=500)

    sentry_init(integrations=[LilyaIntegration()])
    events = capture_events()

    handler_maps = ({500: server_error_handler},)
    _wrap_exception_handler_maps(handler_maps)
    response = await handler_maps[0][500](
        object(), HTTPException(status_code=500, detail="handled")
    )

    assert response.status_code == 500
    (event,) = events
    assert event["exception"]["values"][0]["mechanism"] == {
        "type": "lilya",
        "handled": True,
    }


@pytest.mark.asyncio
async def test_direct_handle_exception_reference_is_patched(
    sentry_init, capture_events
):
    async def server_error_handler(request, exc):
        return PlainText("handled", status_code=500)

    sentry_init(integrations=[LilyaIntegration()])
    events = capture_events()
    sent_messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent_messages.append(message)

    scope = {"type": "http", "method": "GET", "path": "/handled", "headers": []}
    request = Request(scope, receive, send)

    await _direct_handle_exception(
        scope,
        request,
        HTTPException(status_code=500, detail="handled"),
        server_error_handler,
    )

    assert _direct_handle_exception.__name__ == "_sentry_patched_handle_exception"
    assert any(
        message["type"] == "http.response.start" and message["status"] == 500
        for message in sent_messages
    )
    (event,) = events
    assert event["exception"]["values"][0]["mechanism"] == {
        "type": "lilya",
        "handled": True,
    }


def test_json_body_is_attached_without_mutating_request(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[LilyaIntegration()])
    events = capture_events()

    client = TestClient(lilya_app_factory())
    response = client.post(
        "/body/json",
        json={"username": "Jane", "password": "secret"},
        cookies={"session": "abc"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "Jane", "password": "secret"}

    event = _message_from_events(events, "json body")
    assert "session" in event["request"]["cookies"]
    assert event["request"]["data"] == {
        "username": "Jane",
        "password": "[Filtered]",
    }


@pytest.mark.parametrize("json_body", [{}, [], "", False, None])
def test_falsy_json_body_is_attached(sentry_init, capture_events, json_body):
    sentry_init(send_default_pii=True, integrations=[LilyaIntegration()])
    events = capture_events()

    client = TestClient(lilya_app_factory())
    if json_body is None:
        response = client.post(
            "/body/json/echo",
            content=b"null",
            headers={"content-type": "application/json"},
        )
    else:
        response = client.post("/body/json/echo", json=json_body)

    assert response.status_code == 200
    event = _message_from_events(events, "json echo")
    assert event["request"]["data"] == json_body


def test_form_body_is_preserved(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[LilyaIntegration()])
    events = capture_events()

    client = TestClient(lilya_app_factory())
    response = client.post(
        "/body/form",
        data={"username": "Jane", "password": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "Jane"}

    event = _message_from_events(events, "form body")
    assert "data" not in event["request"]


def test_streaming_request_is_preserved(sentry_init):
    sentry_init(integrations=[LilyaIntegration()])

    client = TestClient(lilya_app_factory())
    response = client.post("/body/stream", content=b"hello world")

    assert response.status_code == 200
    assert response.text == "hello world"


def test_streaming_request_with_span_streaming_is_preserved(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration()],
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("span")

    client = TestClient(lilya_app_factory())
    response = client.post("/body/stream", content=b"hello world")
    sentry_sdk.flush()

    assert response.status_code == 200
    assert response.text == "hello world"
    assert items


def test_streaming_body_data_is_attached_to_server_span(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[LilyaIntegration(middleware_spans=True)],
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("span")

    client = TestClient(
        lilya_app_factory(middleware=[DefineMiddleware(SampleMiddleware)])
    )
    response = client.post(
        "/body/json",
        json={"username": "Jane", "password": "secret"},
    )
    sentry_sdk.flush()

    assert response.status_code == 200
    spans = [item.payload for item in items]
    server_span = next(
        span for span in spans if span["attributes"].get("sentry.op") == "http.server"
    )
    assert json.loads(server_span["attributes"][SPANDATA.HTTP_REQUEST_BODY_DATA]) == {
        "username": "Jane",
        "password": "secret",
    }

    middleware_spans = [
        span
        for span in spans
        if span["attributes"].get("sentry.op") == "middleware.lilya"
    ]
    assert middleware_spans
    assert all(
        SPANDATA.HTTP_REQUEST_BODY_DATA not in span["attributes"]
        for span in middleware_spans
    )


def test_streaming_response_is_preserved(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[LilyaIntegration()])
    events = capture_events()

    client = TestClient(lilya_app_factory())
    response = client.get("/streaming-response")

    assert response.status_code == 200
    assert response.text == "hello world"
    assert _transaction_from_events(events)["transaction"] == "/streaming-response"


def test_background_task_is_preserved(sentry_init):
    sentry_init(integrations=[LilyaIntegration()])

    app = lilya_app_factory()
    client = TestClient(app)
    response = client.get("/background")

    assert response.status_code == 200
    assert app.background_calls == ["done"]


def test_user_is_attached_from_lilya_authentication(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[LilyaIntegration()])
    events = capture_events()

    app = lilya_app_factory(
        middleware=[DefineMiddleware(AuthenticationMiddleware, backend=BasicAuth())]
    )
    client = TestClient(app)
    response = client.get("/auth-message", auth=("lilya", "secret"))

    assert response.status_code == 200
    event = _message_from_events(events, "auth message")
    assert event["user"] == {
        "id": "user-1",
        "username": "lilya",
        "email": "lilya@example.com",
    }


def test_user_is_limited_to_sentry_fields(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[LilyaIntegration()])
    events = capture_events()

    app = lilya_app_factory(
        middleware=[DefineMiddleware(AuthenticationMiddleware, backend=RichUserAuth())]
    )
    client = TestClient(app)
    response = client.get("/auth-message")

    assert response.status_code == 200
    event = _message_from_events(events, "auth message")
    assert event["user"] == {
        "id": "user-1",
        "username": "lilya",
        "email": "lilya@example.com",
    }


def test_authentication_error_response_is_preserved(sentry_init):
    sentry_init(integrations=[LilyaIntegration()])

    app = lilya_app_factory(
        middleware=[DefineMiddleware(AuthenticationMiddleware, backend=FailingAuth())]
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/auth-message")

    assert response.status_code == 400
    assert response.text == "authentication failed"


@pytest.mark.asyncio
async def test_authentication_middleware_delegates_to_lilya_call(sentry_init):
    sentry_init(integrations=[LilyaIntegration()])

    assert (
        AuthenticationMiddleware.__call__.__name__
        != "_sentry_patched_authentication_call"
    )
    assert (
        AuthenticationMiddleware.authenticate.__name__ == "_sentry_patched_authenticate"
    )

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    class CustomAuthenticationMiddleware(AuthenticationMiddleware):
        called = False

        async def __call__(self, scope, receive, send):
            self.called = True
            await super().__call__(scope, receive, send)

    middleware = CustomAuthenticationMiddleware(app, backend=BasicAuth())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth",
        "headers": [],
    }

    await middleware(scope, receive, send)

    assert middleware.called is True


@pytest.mark.asyncio
async def test_authentication_middleware_keeps_concurrent_apps_isolated(sentry_init):
    sentry_init(send_default_pii=True, integrations=[LilyaIntegration()])

    both_requests_started = asyncio.Event()
    first_request_can_finish = asyncio.Event()
    second_request_can_finish = asyncio.Event()
    started_paths = []

    async def app(scope, receive, send):
        started_paths.append(scope["path"])
        if len(started_paths) == 2:
            both_requests_started.set()

        if scope["path"] == "/first":
            await first_request_can_finish.wait()
        else:
            await second_request_can_finish.wait()

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    def scope(path, username):
        token = base64.b64encode(("%s:secret" % username).encode("ascii"))
        return {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"authorization", b"Basic " + token)],
        }

    middleware = AuthenticationMiddleware(app, backend=BasicAuth())
    first_request = asyncio.create_task(
        middleware(scope("/first", "first"), receive, send)
    )
    second_request = asyncio.create_task(
        middleware(scope("/second", "second"), receive, send)
    )

    await asyncio.wait_for(both_requests_started.wait(), timeout=1.0)
    first_request_can_finish.set()
    await asyncio.wait_for(first_request, timeout=1.0)
    assert middleware.app is app

    second_request_can_finish.set()
    await asyncio.wait_for(second_request, timeout=1.0)
    assert middleware.app is app


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_spans(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration(middleware_spans=True)],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    app = lilya_app_factory(middleware=[DefineMiddleware(SampleMiddleware)])
    client = TestClient(app, raise_server_exceptions=False)

    if span_streaming:
        items = capture_items("span")
        response = client.get("/message")
        sentry_sdk.flush()

        assert response.status_code == 200
        lilya_spans = [
            item.payload
            for item in items
            if item.payload["attributes"].get("sentry.op") == "middleware.lilya"
        ]
        assert any(
            span["name"] == "SampleMiddleware"
            and span["attributes"]
            == ApproxDict({"middleware.name": "SampleMiddleware"})
            for span in lilya_spans
        )
    else:
        events = capture_events()
        response = client.get("/message")

        assert response.status_code == 200
        transaction = _transaction_from_events(events)
        assert transaction["transaction"] == "/message"
        assert transaction["transaction_info"] == {"source": "route"}
        lilya_spans = [
            span for span in transaction["spans"] if span["op"] == "middleware.lilya"
        ]
        assert any(
            span["description"] == "SampleMiddleware"
            and span["tags"] == {"lilya.middleware_name": "SampleMiddleware"}
            for span in lilya_spans
        )


def test_middleware_spans_disabled(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration(middleware_spans=False)],
    )
    events = capture_events()

    app = lilya_app_factory(middleware=[DefineMiddleware(SampleMiddleware)])
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/message")

    assert response.status_code == 200
    transaction = _transaction_from_events(events)
    assert not [
        span for span in transaction["spans"] if span["op"] == "middleware.lilya"
    ]


@pytest.mark.parametrize(
    "middleware",
    [
        ReceiveSendMiddleware,
        PartialReceiveSendMiddleware,
    ],
)
def test_middleware_receive_send_is_preserved(sentry_init, middleware):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration(middleware_spans=True)],
    )

    app = lilya_app_factory(middleware=[DefineMiddleware(middleware)])
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/message")

    assert response.status_code == 200


def test_websocket_is_preserved(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[LilyaIntegration()])
    events = capture_events()

    client = TestClient(lilya_app_factory())
    with client.websocket_connect("/ws/main") as websocket:
        websocket.send_text("hello")
        assert websocket.receive_text() == "echo:hello"

    transaction = _transaction_from_events(events)
    assert transaction["transaction"] == "/ws/{room}"
    assert transaction["contexts"]["trace"]["op"] == "websocket.server"


def test_websocket_transaction_name_with_middleware_spans(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration(middleware_spans=True)],
    )
    events = capture_events()

    app = lilya_app_factory(middleware=[DefineMiddleware(SampleMiddleware)])
    client = TestClient(app)
    with client.websocket_connect("/ws/main") as websocket:
        websocket.send_text("hello")
        assert websocket.receive_text() == "echo:hello"

    transaction = _transaction_from_events(events)
    assert transaction["transaction"] == "/ws/{room}"
    assert transaction["contexts"]["trace"]["op"] == "websocket.server"


def test_middleware_exception_uses_asgi_transaction_name(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LilyaIntegration(middleware_spans=True)],
    )
    events = capture_events()

    app = lilya_app_factory(middleware=[DefineMiddleware(RaisingMiddleware)])
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/message")

    assert response.status_code == 500
    transaction = _transaction_from_events(events)
    assert transaction["transaction"].endswith("/message")
    assert transaction["transaction"] != "generic Lilya request"
    assert transaction["transaction_info"] == {"source": "url"}


def test_lifespan_is_preserved(sentry_init):
    state = []

    class Lifespan:
        async def __aenter__(self):
            state.append("startup")

        async def __aexit__(self, exc_type, exc, tb):
            state.append("shutdown")

    def lifespan(app):
        return Lifespan()

    sentry_init(integrations=[LilyaIntegration()])

    app = lilya_app_factory(lifespan=lifespan)
    with TestClient(app) as client:
        assert state == ["startup"]
        response = client.get("/message")
        assert response.status_code == 200

    assert state == ["startup", "shutdown"]

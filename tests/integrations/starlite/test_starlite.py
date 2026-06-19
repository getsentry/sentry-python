from __future__ import annotations

import functools
from typing import Any, Dict

import pytest
from starlite import AbstractMiddleware, Controller, LoggingConfig, Starlite, get
from starlite.middleware import LoggingMiddlewareConfig, RateLimitConfig
from starlite.middleware.session.memory_backend import MemoryBackendConfig
from starlite.testing import TestClient

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.starlite import StarliteIntegration


def starlite_app_factory(middleware=None, debug=True, exception_handlers=None):
    class MyController(Controller):
        path = "/controller"

        @get("/error")
        async def controller_error(self) -> None:
            raise Exception("Whoa")

    @get("/some_url")
    async def homepage_handler() -> "Dict[str, Any]":
        1 / 0
        return {"status": "ok"}

    @get("/custom_error", name="custom_name")
    async def custom_error() -> Any:
        raise Exception("Too Hot")

    @get("/message")
    async def message() -> "Dict[str, Any]":
        capture_message("hi")
        return {"status": "ok"}

    @get("/message/{message_id:str}")
    async def message_with_id() -> "Dict[str, Any]":
        capture_message("hi")
        return {"status": "ok"}

    @get("/nomessage")
    async def nomessage() -> "Dict[str, Any]":
        return {"status": "ok"}

    logging_config = LoggingConfig()

    app = Starlite(
        route_handlers=[
            homepage_handler,
            custom_error,
            message,
            message_with_id,
            nomessage,
            MyController,
        ],
        debug=debug,
        middleware=middleware,
        logging_config=logging_config,
        exception_handlers=exception_handlers,
    )

    return app


@pytest.mark.parametrize(
    "test_url,expected_error,expected_message,expected_tx_name",
    [
        (
            "/some_url",
            ZeroDivisionError,
            "division by zero",
            "tests.integrations.starlite.test_starlite.starlite_app_factory.<locals>.homepage_handler",
        ),
        (
            "/custom_error",
            Exception,
            "Too Hot",
            "custom_name",
        ),
        (
            "/controller/error",
            Exception,
            "Whoa",
            "partial(<function tests.integrations.starlite.test_starlite.starlite_app_factory.<locals>.MyController.controller_error>)",
        ),
    ],
)
def test_catch_exceptions(
    sentry_init,
    capture_exceptions,
    capture_events,
    test_url,
    expected_error,
    expected_message,
    expected_tx_name,
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
    assert event["transaction"] == expected_tx_name
    assert event["exception"]["values"][0]["mechanism"]["type"] == "starlite"


@pytest.mark.parametrize(
    "test_url,expected_tx_name",
    [
        (
            "/some_url",
            "tests.integrations.starlite.test_starlite.starlite_app_factory.<locals>.homepage_handler",
        ),
        (
            "/custom_error",
            "custom_name",
        ),
        (
            "/controller/error",
            "partial(<function tests.integrations.starlite.test_starlite.starlite_app_factory.<locals>.MyController.controller_error>)",
        ),
    ],
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_transaction_name_and_source(
    sentry_init,
    capture_events,
    test_url,
    expected_tx_name,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    starlite_app = starlite_app_factory()
    client = TestClient(starlite_app)

    if span_streaming:
        items = capture_items("span")

        try:
            client.get(test_url)
        except Exception:
            pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        spans = [span for span in spans if expected_tx_name in span["name"]]
        assert len(spans) == 1
        assert spans[0]["attributes"]["sentry.span.source"] == "component"
    else:
        events = capture_events()

        try:
            client.get(test_url)
        except Exception:
            pass

        (_, transaction) = events
        assert expected_tx_name in transaction["transaction"]
        assert transaction["transaction_info"] == {"source": "component"}


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_spans(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
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

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client = TestClient(
        starlite_app, raise_server_exceptions=False, base_url="http://testserver.local"
    )
    client.get("/message")

    expected = {"SessionMiddleware", "LoggingMiddleware", "RateLimitMiddleware"}

    if span_streaming:
        sentry_sdk.flush()

        middleware_spans = [
            item.payload
            for item in items
            if item.payload.get("attributes", {}).get("sentry.op")
            == "middleware.starlite"
        ]
        assert len(middleware_spans) == 3

        found = set()
        for span in middleware_spans:
            assert span["name"] in expected
            assert span["name"] not in found
            found.add(span["name"])
            assert span["name"] == span["attributes"]["middleware.name"]
    else:
        (_, transaction_event) = events

        found = set()
        middleware_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == "middleware.starlite"
        ]
        assert len(middleware_spans) == 3

        for span in middleware_spans:
            assert span["description"] in expected
            assert span["description"] not in found
            found.add(span["description"])
            assert span["description"] == span["tags"]["starlite.middleware_name"]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_callback_spans(
    sentry_init, capture_events, capture_items, span_streaming
):
    class SampleMiddleware(AbstractMiddleware):
        async def __call__(self, scope, receive, send) -> None:
            async def do_stuff(message):
                if message["type"] == "http.response.start":
                    # do something here.
                    pass
                await send(message)

            await self.app(scope, receive, do_stuff)

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    starlite_app = starlite_app_factory(middleware=[SampleMiddleware])

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client = TestClient(starlite_app, raise_server_exceptions=False)
    client.get("/message")

    expected_starlite_spans = [
        {
            "op": "middleware.starlite",
            "description": "SampleMiddleware",
            "tags": {"starlite.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.starlite.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"starlite.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.starlite.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"starlite.middleware_name": "SampleMiddleware"},
        },
    ]

    if span_streaming:
        sentry_sdk.flush()

        actual_starlite_spans = [
            item.payload
            for item in items
            if "middleware.starlite"
            in item.payload.get("attributes", {}).get("sentry.op", "")
        ]
        assert len(actual_starlite_spans) == 3

        def is_matching_span_streaming(expected_span, actual_span):
            return (
                expected_span["op"] == actual_span["attributes"]["sentry.op"]
                and expected_span["description"] == actual_span["name"]
                and expected_span["tags"]["starlite.middleware_name"]
                == actual_span["attributes"]["middleware.name"]
            )

        for expected_span in expected_starlite_spans:
            assert any(
                is_matching_span_streaming(expected_span, actual_span)
                for actual_span in actual_starlite_spans
            )
    else:
        (_, transaction_events) = events

        def is_matching_span(expected_span, actual_span):
            return (
                expected_span["op"] == actual_span["op"]
                and expected_span["description"] == actual_span["description"]
                and expected_span["tags"] == actual_span["tags"]
            )

        actual_starlite_spans = list(
            span
            for span in transaction_events["spans"]
            if "middleware.starlite" in span["op"]
        )
        assert len(actual_starlite_spans) == 3

        for expected_span in expected_starlite_spans:
            assert any(
                is_matching_span(expected_span, actual_span)
                for actual_span in actual_starlite_spans
            )


def test_middleware_receive_send(sentry_init, capture_events):
    class SampleReceiveSendMiddleware(AbstractMiddleware):
        async def __call__(self, scope, receive, send):
            message = await receive()
            assert message
            assert message["type"] == "http.request"

            send_output = await send({"type": "something-unimportant"})
            assert send_output is None

            await self.app(scope, receive, send)

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
    )
    starlite_app = starlite_app_factory(middleware=[SampleReceiveSendMiddleware])

    client = TestClient(starlite_app, raise_server_exceptions=False)
    # See SampleReceiveSendMiddleware.__call__ above for assertions of correct behavior
    client.get("/message")


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_partial_receive_send(
    sentry_init, capture_events, capture_items, span_streaming
):
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

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    starlite_app = starlite_app_factory(middleware=[SamplePartialReceiveSendMiddleware])

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client = TestClient(starlite_app, raise_server_exceptions=False)
    # See SamplePartialReceiveSendMiddleware.__call__ above for assertions of correct behavior
    client.get("/message")

    expected_starlite_spans = [
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
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"starlite.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
    ]

    if span_streaming:
        sentry_sdk.flush()

        actual_starlite_spans = [
            item.payload
            for item in items
            if "middleware.starlite"
            in item.payload.get("attributes", {}).get("sentry.op", "")
        ]
        assert len(actual_starlite_spans) == 3

        def is_matching_span_streaming(expected_span, actual_span):
            return (
                expected_span["op"] == actual_span["attributes"]["sentry.op"]
                and actual_span["name"].startswith(expected_span["description"])
                and expected_span["tags"]["starlite.middleware_name"]
                == actual_span["attributes"]["middleware.name"]
            )

        for expected_span in expected_starlite_spans:
            assert any(
                is_matching_span_streaming(expected_span, actual_span)
                for actual_span in actual_starlite_spans
            )
    else:
        (_, transaction_events) = events

        def is_matching_span(expected_span, actual_span):
            return (
                expected_span["op"] == actual_span["op"]
                and actual_span["description"].startswith(expected_span["description"])
                and expected_span["tags"] == actual_span["tags"]
            )

        actual_starlite_spans = list(
            span
            for span in transaction_events["spans"]
            if "middleware.starlite" in span["op"]
        )
        assert len(actual_starlite_spans) == 3

        for expected_span in expected_starlite_spans:
            assert any(
                is_matching_span(expected_span, actual_span)
                for actual_span in actual_starlite_spans
            )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[StarliteIntegration()],
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
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

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

    client = TestClient(
        starlite_app, raise_server_exceptions=False, base_url="http://testserver.local"
    )
    client.get("/message")

    if span_streaming:
        sentry_sdk.flush()

        starlite_items = [
            item
            for item in items
            if "starlite" in item.payload.get("attributes", {}).get("sentry.op", "")
        ]
        assert len(starlite_items) > 0
        for item in starlite_items:
            assert item.payload["attributes"]["sentry.origin"] == "auto.http.starlite"
    else:
        (_, event) = events

        assert event["contexts"]["trace"]["origin"] == "auto.http.starlite"
        starlite_spans = [span for span in event["spans"] if "starlite" in span["op"]]
        assert len(starlite_spans) > 0
        for span in starlite_spans:
            assert span["origin"] == "auto.http.starlite"


@pytest.mark.parametrize(
    "is_send_default_pii",
    [
        True,
        False,
    ],
    ids=[
        "send_default_pii=True",
        "send_default_pii=False",
    ],
)
def test_starlite_scope_user_on_exception_event(
    sentry_init, capture_exceptions, capture_events, is_send_default_pii
):
    class TestUserMiddleware(AbstractMiddleware):
        async def __call__(self, scope, receive, send):
            scope["user"] = {
                "email": "lennon@thebeatles.com",
                "username": "john",
                "id": "1",
            }
            await self.app(scope, receive, send)

    sentry_init(
        integrations=[StarliteIntegration()], send_default_pii=is_send_default_pii
    )
    starlite_app = starlite_app_factory(middleware=[TestUserMiddleware])
    exceptions = capture_exceptions()
    events = capture_events()

    # This request intentionally raises an exception
    client = TestClient(starlite_app)
    try:
        client.get("/some_url")
    except Exception:
        pass

    assert len(exceptions) == 1
    assert len(events) == 1
    (event,) = events

    if is_send_default_pii:
        assert "user" in event
        assert event["user"] == {
            "email": "lennon@thebeatles.com",
            "username": "john",
            "id": "1",
        }
    else:
        assert "user" not in event


@pytest.mark.parametrize("span_streaming", [True, False])
def test_request_url(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[StarliteIntegration()],
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    starlite_app = starlite_app_factory()
    client = TestClient(starlite_app, root_path="/root")

    if span_streaming:
        items = capture_items("span")

        client.get("/nomessage")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        (server_span,) = (
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "http.server"
        )
        assert server_span["attributes"]["url.full"] == (
            "http://testserver/root/nomessage"
        )
    else:
        events = capture_events()

        client.get("/nomessage")

        (event,) = events
        assert event["request"]["url"] == "http://testserver/root/nomessage"

from __future__ import annotations
import functools

import pytest

from sentry_sdk import capture_message
from sentry_sdk.integrations.starlite import StarliteIntegration
from tests.conftest import ApproxDict

from typing import Any, Dict

from starlite import AbstractMiddleware, LoggingConfig, Starlite, get, Controller
from starlite.middleware import LoggingMiddlewareConfig, RateLimitConfig
from starlite.middleware.session.memory_backend import MemoryBackendConfig
from starlite.testing import TestClient


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

    logging_config = LoggingConfig()

    app = Starlite(
        route_handlers=[
            homepage_handler,
            custom_error,
            message,
            message_with_id,
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
    client.get("/message")

    (_, transaction_event) = events

    expected = {"SessionMiddleware", "LoggingMiddleware", "RateLimitMiddleware"}
    found = set()

    starlite_spans = (
        span
        for span in transaction_event["spans"]
        if span["op"] == "middleware.starlite"
    )

    for span in starlite_spans:
        assert span["description"] in expected
        assert span["description"] not in found
        found.add(span["description"])
        assert span["description"] == span["tags"]["starlite.middleware_name"]


def test_middleware_callback_spans(sentry_init, capture_events):
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
    )
    starlite_app = starlite_app_factory(middleware=[SampleMiddleware])
    events = capture_events()

    client = TestClient(starlite_app, raise_server_exceptions=False)
    client.get("/message")

    (_, transaction_events) = events

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

    def is_matching_span(expected_span, actual_span):
        return (
            expected_span["op"] == actual_span["op"]
            and expected_span["description"] == actual_span["description"]
            and ApproxDict(expected_span["tags"]) == actual_span["tags"]
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


def test_middleware_partial_receive_send(sentry_init, capture_events):
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
    )
    starlite_app = starlite_app_factory(middleware=[SamplePartialReceiveSendMiddleware])
    events = capture_events()

    client = TestClient(starlite_app, raise_server_exceptions=False)
    # See SamplePartialReceiveSendMiddleware.__call__ above for assertions of correct behavior
    client.get("/message")

    (_, transaction_events) = events

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

    def is_matching_span(expected_span, actual_span):
        return (
            expected_span["op"] == actual_span["op"]
            and actual_span["description"].startswith(expected_span["description"])
            and ApproxDict(expected_span["tags"]) == actual_span["tags"]
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


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[StarliteIntegration()],
        traces_sample_rate=1.0,
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
    client.get("/message")

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.starlite"
    for span in event["spans"]:
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

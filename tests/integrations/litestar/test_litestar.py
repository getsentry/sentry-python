from __future__ import annotations

import functools
from typing import Any

import pytest
from litestar import Controller, Litestar, get, post
from litestar.exceptions import HTTPException
from litestar.logging.config import LoggingConfig
from litestar.middleware import AbstractMiddleware
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.middleware.session.server_side import ServerSideSessionConfig
from litestar.testing import TestClient

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.litestar import LitestarIntegration
from tests.conftest import ApproxDict
from tests.integrations.conftest import parametrize_test_configurable_status_codes
from tests.integrations.utils import DATA_COLLECTION_USER_INFO_CASES


def litestar_app_factory(middleware=None, debug=True, exception_handlers=None):
    class MyController(Controller):
        path = "/controller"

        @get("/error")
        async def controller_error(self) -> None:
            raise Exception("Whoa")

    @get("/some_url")
    async def homepage_handler() -> "dict[str, Any]":
        1 / 0
        return {"status": "ok"}

    @get("/custom_error", name="custom_name")
    async def custom_error() -> Any:
        raise Exception("Too Hot")

    @get("/message")
    async def message() -> "dict[str, Any]":
        capture_message("hi")
        return {"status": "ok"}

    @get("/message/{message_id:str}")
    async def message_with_id() -> "dict[str, Any]":
        capture_message("hi")
        return {"status": "ok"}

    @post("/body/json")
    async def body_json(data: "dict[str, Any]") -> "dict[str, Any]":
        capture_message("hi")
        return {"status": "ok"}

    logging_config = LoggingConfig()

    app = Litestar(
        route_handlers=[
            homepage_handler,
            custom_error,
            message,
            message_with_id,
            body_json,
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
            "tests.integrations.litestar.test_litestar.litestar_app_factory.<locals>.homepage_handler",
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
            "tests.integrations.litestar.test_litestar.litestar_app_factory.<locals>.MyController.controller_error",
        ),
    ],
)
def test_catch_exceptions(
    sentry_init,
    capture_exceptions,
    capture_items,
    test_url,
    expected_error,
    expected_message,
    expected_tx_name,
):
    sentry_init(
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
    )
    litestar_app = litestar_app_factory()
    client = TestClient(litestar_app)
    exceptions = capture_exceptions()
    items = capture_items("event")

    try:
        client.get(test_url)
    except Exception:
        pass

    (exc,) = exceptions
    assert isinstance(exc, expected_error)
    assert str(exc) == expected_message

    (event,) = (item.payload for item in items)
    assert expected_tx_name in event["transaction"]
    assert event["exception"]["values"][0]["mechanism"]["type"] == "litestar"


@pytest.mark.parametrize(
    "test_url,expected_tx_name",
    [
        (
            "/some_url",
            "tests.integrations.litestar.test_litestar.litestar_app_factory.<locals>.homepage_handler",
        ),
        (
            "/custom_error",
            "custom_name",
        ),
        (
            "/controller/error",
            "tests.integrations.litestar.test_litestar.litestar_app_factory.<locals>.MyController.controller_error",
        ),
    ],
)
def test_segment_name_and_source(
    sentry_init,
    test_url,
    expected_tx_name,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
    )
    litestar_app = litestar_app_factory()
    client = TestClient(litestar_app)
    items = capture_items("span")

    try:
        client.get(test_url)
    except Exception:
        pass

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    spans = [span for span in spans if expected_tx_name in span["name"]]
    assert len(spans) == 1
    assert spans[0]["attributes"]["sentry.segment.name.source"] == "component"


def test_middleware_spans(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
    )

    logging_config = LoggingMiddlewareConfig()
    session_config = ServerSideSessionConfig()
    rate_limit_config = RateLimitConfig(rate_limit=("hour", 5))

    litestar_app = litestar_app_factory(
        middleware=[
            session_config.middleware,
            logging_config.middleware,
            rate_limit_config.middleware,
        ]
    )
    client = TestClient(
        litestar_app, raise_server_exceptions=False, base_url="http://testserver.local"
    )
    items = capture_items("span")

    client.get("/message")

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    expected = {"SessionMiddleware", "LoggingMiddleware", "RateLimitMiddleware"}
    found = set()

    litestar_spans = (
        span
        for span in spans
        if span["attributes"]["sentry.op"] == "middleware.litestar"
    )

    for span in litestar_spans:
        assert span["name"] in expected
        assert span["name"] not in found
        found.add(span["name"])
        assert span["name"] == span["attributes"]["middleware.name"]


def test_middleware_callback_spans(
    sentry_init,
    capture_items,
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
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
    )

    litestar_app = litestar_app_factory(middleware=[SampleMiddleware])
    client = TestClient(litestar_app, raise_server_exceptions=False)
    items = capture_items("span")

    client.get("/message")

    spans = [item.payload for item in items]

    expected_litestar_spans = [
        {
            "name": "SampleMiddleware",
            "attributes": ApproxDict(
                {
                    "middleware.name": "SampleMiddleware",
                    "sentry.op": "middleware.litestar",
                },
            ),
        },
        {
            "name": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "attributes": ApproxDict(
                {
                    "middleware.name": "SampleMiddleware",
                    "sentry.op": "middleware.litestar.send",
                }
            ),
        },
        {
            "name": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "attributes": ApproxDict(
                {
                    "middleware.name": "SampleMiddleware",
                    "sentry.op": "middleware.litestar.send",
                }
            ),
        },
    ]

    def is_matching_span(expected_span, actual_span):
        return (
            expected_span["name"] == actual_span["name"]
            and expected_span["attributes"] == actual_span["attributes"]
        )

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    actual_litestar_spans = list(
        span
        for span in spans
        if "middleware.litestar" in span["attributes"].get("sentry.op")
    )

    assert len(actual_litestar_spans) == 3

    for expected_span in expected_litestar_spans:
        assert any(
            is_matching_span(expected_span, actual_span)
            for actual_span in actual_litestar_spans
        )


def test_middleware_receive_send(
    sentry_init,
):
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
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
    )
    litestar_app = litestar_app_factory(middleware=[SampleReceiveSendMiddleware])

    client = TestClient(litestar_app, raise_server_exceptions=False)
    # See SampleReceiveSendMiddleware.__call__ above for assertions of correct behavior
    client.get("/message")


def test_middleware_partial_receive_send(
    sentry_init,
    capture_items,
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
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
    )

    litestar_app = litestar_app_factory(middleware=[SamplePartialReceiveSendMiddleware])
    client = TestClient(litestar_app, raise_server_exceptions=False)
    items = capture_items("span")

    # See SamplePartialReceiveSendMiddleware.__call__ above for assertions of correct behavior
    client.get("/message")

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    expected_litestar_spans = [
        {
            "name": "SamplePartialReceiveSendMiddleware",
            "attributes": ApproxDict(
                {
                    "middleware.name": "SamplePartialReceiveSendMiddleware",
                    "sentry.op": "middleware.litestar",
                }
            ),
        },
        {
            "name": "TestClientTransport.create_receive.<locals>.receive",
            "attributes": ApproxDict(
                {
                    "middleware.name": "SamplePartialReceiveSendMiddleware",
                    "sentry.op": "middleware.litestar.receive",
                }
            ),
        },
        {
            "name": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "attributes": ApproxDict(
                {
                    "middleware.name": "SamplePartialReceiveSendMiddleware",
                    "sentry.op": "middleware.litestar.send",
                }
            ),
        },
    ]

    def is_matching_span(expected_span, actual_span):
        return (
            actual_span["name"].startswith(expected_span["name"])
            and expected_span["attributes"] == actual_span["attributes"]
        )

    actual_litestar_spans = list(
        span
        for span in spans
        if "middleware.litestar" in span["attributes"].get("sentry.op")
    )
    assert len(actual_litestar_spans) == 3

    for expected_span in expected_litestar_spans:
        assert any(
            is_matching_span(expected_span, actual_span)
            for actual_span in actual_litestar_spans
        )


def test_span_origin(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[LitestarIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    logging_config = LoggingMiddlewareConfig()
    session_config = ServerSideSessionConfig()
    rate_limit_config = RateLimitConfig(rate_limit=("hour", 5))

    litestar_app = litestar_app_factory(
        middleware=[
            session_config.middleware,
            logging_config.middleware,
            rate_limit_config.middleware,
        ]
    )
    client = TestClient(
        litestar_app, raise_server_exceptions=False, base_url="http://testserver.local"
    )
    items = capture_items("span")

    client.get("/message")

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    for span in spans:
        if span["attributes"]["sentry.origin"] == "auto.http.httpx":
            continue
        assert span["attributes"]["sentry.origin"] == "auto.http.litestar"


@pytest.mark.parametrize("init_kwargs, expect_user", DATA_COLLECTION_USER_INFO_CASES)
def test_litestar_scope_user_on_exception_event(
    sentry_init,
    capture_exceptions,
    capture_items,
    init_kwargs,
    expect_user,
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
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
        **init_kwargs,
    )

    litestar_app = litestar_app_factory(middleware=[TestUserMiddleware])
    client = TestClient(litestar_app)
    exceptions = capture_exceptions()
    items = capture_items("event")

    # This request intentionally raises an exception
    try:
        client.get("/some_url")
    except Exception:
        pass

    assert len(exceptions) == 1
    (event,) = (item.payload for item in items)

    if expect_user:
        assert "user" in event
        assert event["user"] == {
            "email": "lennon@thebeatles.com",
            "username": "john",
            "id": "1",
        }
    else:
        assert "user" not in event


COOKIE_HEADER = "jwt=tokenval; theme=dark; lang=en; identity=alice"


@parametrize_test_configurable_status_codes
def test_configurable_status_codes_handler(
    sentry_init,
    capture_items,
    failed_request_status_codes,
    status_code,
    expected_error,
):
    integration_kwargs = (
        {"failed_request_status_codes": failed_request_status_codes}
        if failed_request_status_codes is not None
        else {}
    )
    sentry_init(
        integrations=[LitestarIntegration(**integration_kwargs)],
        trace_lifecycle="stream",
    )

    @get("/error")
    async def error() -> None:
        raise HTTPException(status_code=status_code)

    app = Litestar([error])
    client = TestClient(app)
    items = capture_items("event")

    client.get("/error")

    events = [item.payload for item in items]

    assert len(events) == int(expected_error)


@parametrize_test_configurable_status_codes
def test_configurable_status_codes_middleware(
    sentry_init,
    capture_items,
    failed_request_status_codes,
    status_code,
    expected_error,
):
    integration_kwargs = (
        {"failed_request_status_codes": failed_request_status_codes}
        if failed_request_status_codes is not None
        else {}
    )

    sentry_init(
        integrations=[LitestarIntegration(**integration_kwargs)],
        trace_lifecycle="stream",
    )

    def create_raising_middleware(app):
        async def raising_middleware(scope, receive, send):
            raise HTTPException(status_code=status_code)

        return raising_middleware

    @get("/error")
    async def error() -> None: ...

    app = Litestar([error], middleware=[create_raising_middleware])
    client = TestClient(app)
    items = capture_items("event")

    client.get("/error")

    events = [item.payload for item in items]

    assert len(events) == int(expected_error)


def test_catch_non_http_exceptions_in_middleware(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[LitestarIntegration()],
        trace_lifecycle="stream",
    )

    def create_raising_middleware(app):
        async def raising_middleware(scope, receive, send):
            raise RuntimeError("Too Hot")

        return raising_middleware

    @get("/error")
    async def error() -> None: ...

    app = Litestar([error], middleware=[create_raising_middleware])
    client = TestClient(app)
    items = capture_items("event")

    try:
        client.get("/error")
    except RuntimeError:
        pass

    events = [item.payload for item in items]

    assert len(events) == 1
    event_exception = events[0]["exception"]["values"][0]
    assert event_exception["type"] == "RuntimeError"
    assert event_exception["value"] == "Too Hot"

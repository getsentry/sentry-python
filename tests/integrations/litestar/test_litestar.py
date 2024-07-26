import functools

import pytest

from sentry_sdk import capture_message
from sentry_sdk.integrations.litestar import LitestarIntegration

from typing import Any, Dict

from litestar import Litestar, get, Controller
from litestar.logging.config import LoggingConfig
from litestar.middleware import AbstractMiddleware
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.middleware.session.server_side import ServerSideSessionConfig
from litestar.testing import TestClient


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


def litestar_app_factory(middleware=None, debug=True, exception_handlers=None):
    class MyController(Controller):
        path = "/controller"

        @get("/error")
        async def controller_error(self) -> None:
            raise Exception("Whoa")

    @get("/some_url")
    async def homepage_handler() -> Dict[str, Any]:
        1 / 0
        return {"status": "ok"}

    @get("/custom_error", name="custom_name")
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

    app = Litestar(
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
    capture_events,
    test_url,
    expected_error,
    expected_message,
    expected_tx_name,
):
    sentry_init(integrations=[LitestarIntegration()])
    litestar_app = litestar_app_factory()
    exceptions = capture_exceptions()
    events = capture_events()

    client = TestClient(litestar_app)
    try:
        client.get(test_url)
    except Exception:
        pass

    (exc,) = exceptions
    assert isinstance(exc, expected_error)
    assert str(exc) == expected_message

    (event,) = events
    assert event["transaction"] == expected_tx_name
    assert event["exception"]["values"][0]["mechanism"]["type"] == "litestar"


def test_middleware_spans(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LitestarIntegration()],
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
    events = capture_events()

    client = TestClient(
        litestar_app, raise_server_exceptions=False, base_url="http://testserver.local"
    )
    try:
        client.get("/message")
    except Exception:
        pass

    (_, transaction_event) = events

    expected = ["SessionMiddleware", "LoggingMiddleware", "RateLimitMiddleware"]

    idx = 0
    for span in transaction_event["spans"]:
        if span["op"] == "middleware.litestar":
            assert span["description"] == expected[idx]
            assert span["tags"]["litestar.middleware_name"] == expected[idx]
            idx += 1


def test_middleware_callback_spans(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LitestarIntegration()],
    )
    litestar_app = litestar_app_factory(middleware=[SampleMiddleware])
    events = capture_events()

    client = TestClient(litestar_app, raise_server_exceptions=False)
    try:
        client.get("/message")
    except Exception:
        pass

    (_, transaction_event) = events

    expected = [
        {
            "op": "middleware.litestar",
            "description": "SampleMiddleware",
            "tags": {"litestar.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.litestar.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"litestar.middleware_name": "SampleMiddleware"},
        },
        {
            "op": "middleware.litestar.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"litestar.middleware_name": "SampleMiddleware"},
        },
    ]
    for idx, span in enumerate(transaction_event["spans"]):
        assert span["op"] == expected[idx]["op"]
        assert span["description"] == expected[idx]["description"]
        assert span["tags"] == expected[idx]["tags"]


def test_middleware_receive_send(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LitestarIntegration()],
    )
    litestar_app = litestar_app_factory(middleware=[SampleReceiveSendMiddleware])

    client = TestClient(litestar_app, raise_server_exceptions=False)
    try:
        # NOTE: the assert statements checking
        # for correct behaviour are in `SampleReceiveSendMiddleware`!
        client.get("/message")
    except Exception:
        pass


def test_middleware_partial_receive_send(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LitestarIntegration()],
    )
    litestar_app = litestar_app_factory(middleware=[SamplePartialReceiveSendMiddleware])
    events = capture_events()

    client = TestClient(litestar_app, raise_server_exceptions=False)
    try:
        client.get("/message")
    except Exception:
        pass

    (_, transaction_event) = events

    expected = [
        {
            "op": "middleware.litestar",
            "description": "SamplePartialReceiveSendMiddleware",
            "tags": {"litestar.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
        {
            "op": "middleware.litestar.receive",
            "description": "TestClientTransport.create_receive.<locals>.receive",
            "tags": {"litestar.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
        {
            "op": "middleware.litestar.send",
            "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
            "tags": {"litestar.middleware_name": "SamplePartialReceiveSendMiddleware"},
        },
    ]

    for idx, span in enumerate(transaction_event["spans"]):
        assert span["op"] == expected[idx]["op"]
        assert span["description"].startswith(expected[idx]["description"])
        assert span["tags"] == expected[idx]["tags"]


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[LitestarIntegration()],
        traces_sample_rate=1.0,
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
    events = capture_events()

    client = TestClient(
        litestar_app, raise_server_exceptions=False, base_url="http://testserver.local"
    )
    try:
        client.get("/message")
    except Exception:
        pass

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.litestar"
    for span in event["spans"]:
        assert span["origin"] == "auto.http.litestar"
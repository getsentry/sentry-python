from __future__ import annotations

import functools
from typing import Any

import pytest
from litestar import Controller, Litestar, get
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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_catch_exceptions(
    sentry_init,
    capture_exceptions,
    capture_events,
    capture_items,
    test_url,
    expected_error,
    expected_message,
    expected_tx_name,
    span_streaming,
):
    sentry_init(
        integrations=[LitestarIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    litestar_app = litestar_app_factory()
    client = TestClient(litestar_app)
    exceptions = capture_exceptions()
    if span_streaming:
        items = capture_items("event")

        try:
            client.get(test_url)
        except Exception:
            pass

        (exc,) = exceptions
        assert isinstance(exc, expected_error)
        assert str(exc) == expected_message

        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        try:
            client.get(test_url)
        except Exception:
            pass

        (exc,) = exceptions
        assert isinstance(exc, expected_error)
        assert str(exc) == expected_message

        (event,) = events
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
        integrations=[LitestarIntegration()],
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )
    litestar_app = litestar_app_factory()
    client = TestClient(litestar_app)

    if span_streaming:
        items = capture_items("span")

        try:
            client.get(test_url)
        except Exception:
            pass

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        spans = [span for span in spans if span["name"] == expected_tx_name]
        assert len(spans) == 1
        assert spans[0]["attributes"]["sentry.span.source"] == "component"
    else:
        events = capture_events()

        try:
            client.get(test_url)
        except Exception:
            pass

        (_, transaction) = events
        assert transaction["transaction"] == expected_tx_name
        assert transaction["transaction_info"] == {"source": "component"}


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_spans(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[LitestarIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
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
    if span_streaming:
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
    else:
        events = capture_events()

        client.get("/message")

        (_, transaction_event) = events

        expected = {"SessionMiddleware", "LoggingMiddleware", "RateLimitMiddleware"}
        found = set()

        litestar_spans = (
            span
            for span in transaction_event["spans"]
            if span["op"] == "middleware.litestar"
        )

        for span in litestar_spans:
            assert span["description"] in expected
            assert span["description"] not in found
            found.add(span["description"])
            assert span["description"] == span["tags"]["litestar.middleware_name"]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_callback_spans(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
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
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    litestar_app = litestar_app_factory(middleware=[SampleMiddleware])
    client = TestClient(litestar_app, raise_server_exceptions=False)
    if span_streaming:
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
    else:
        events = capture_events()

        client.get("/message")

        (_, transaction_events) = events

        expected_litestar_spans = [
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

        def is_matching_span(expected_span, actual_span):
            return (
                expected_span["op"] == actual_span["op"]
                and expected_span["description"] == actual_span["description"]
                and expected_span["tags"] == actual_span["tags"]
            )

        actual_litestar_spans = list(
            span
            for span in transaction_events["spans"]
            if "middleware.litestar" in span["op"]
        )

    assert len(actual_litestar_spans) == 3

    for expected_span in expected_litestar_spans:
        assert any(
            is_matching_span(expected_span, actual_span)
            for actual_span in actual_litestar_spans
        )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_receive_send(sentry_init, capture_items, span_streaming):
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
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    litestar_app = litestar_app_factory(middleware=[SampleReceiveSendMiddleware])

    client = TestClient(litestar_app, raise_server_exceptions=False)
    # See SampleReceiveSendMiddleware.__call__ above for assertions of correct behavior
    client.get("/message")


@pytest.mark.parametrize("span_streaming", [True, False])
def test_middleware_partial_receive_send(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
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
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    litestar_app = litestar_app_factory(middleware=[SamplePartialReceiveSendMiddleware])
    client = TestClient(litestar_app, raise_server_exceptions=False)

    if span_streaming:
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
    else:
        events = capture_events()

        # See SamplePartialReceiveSendMiddleware.__call__ above for assertions of correct behavior
        client.get("/message")

        (_, transaction_events) = events

        expected_litestar_spans = [
            {
                "op": "middleware.litestar",
                "description": "SamplePartialReceiveSendMiddleware",
                "tags": {
                    "litestar.middleware_name": "SamplePartialReceiveSendMiddleware"
                },
            },
            {
                "op": "middleware.litestar.receive",
                "description": "TestClientTransport.create_receive.<locals>.receive",
                "tags": {
                    "litestar.middleware_name": "SamplePartialReceiveSendMiddleware"
                },
            },
            {
                "op": "middleware.litestar.send",
                "description": "SentryAsgiMiddleware._run_app.<locals>._sentry_wrapped_send",
                "tags": {
                    "litestar.middleware_name": "SamplePartialReceiveSendMiddleware"
                },
            },
        ]

        def is_matching_span(expected_span, actual_span):
            return (
                expected_span["op"] == actual_span["op"]
                and actual_span["description"].startswith(expected_span["description"])
                and expected_span["tags"] == actual_span["tags"]
            )

        actual_litestar_spans = list(
            span
            for span in transaction_events["spans"]
            if "middleware.litestar" in span["op"]
        )
    assert len(actual_litestar_spans) == 3

    for expected_span in expected_litestar_spans:
        assert any(
            is_matching_span(expected_span, actual_span)
            for actual_span in actual_litestar_spans
        )


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[LitestarIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
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
    if span_streaming:
        items = capture_items("span")

        client.get("/message")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        for span in spans:
            if span["attributes"]["sentry.origin"] == "auto.http.httpx":
                continue
            assert span["attributes"]["sentry.origin"] == "auto.http.litestar"
    else:
        events = capture_events()

        client.get("/message")

        (_, event) = events

        assert event["contexts"]["trace"]["origin"] == "auto.http.litestar"
        for span in event["spans"]:
            assert span["origin"] == "auto.http.litestar"


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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_litestar_scope_user_on_exception_event(
    sentry_init,
    capture_exceptions,
    capture_events,
    capture_items,
    is_send_default_pii,
    span_streaming,
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
        send_default_pii=is_send_default_pii,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    litestar_app = litestar_app_factory(middleware=[TestUserMiddleware])
    client = TestClient(litestar_app)
    exceptions = capture_exceptions()
    if span_streaming:
        items = capture_items("event")

        # This request intentionally raises an exception
        try:
            client.get("/some_url")
        except Exception:
            pass

        assert len(exceptions) == 1
        (event,) = (item.payload for item in items)
    else:
        events = capture_events()

        # This request intentionally raises an exception
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


@parametrize_test_configurable_status_codes
@pytest.mark.parametrize("span_streaming", [True, False])
def test_configurable_status_codes_handler(
    sentry_init,
    capture_events,
    capture_items,
    failed_request_status_codes,
    status_code,
    expected_error,
    span_streaming,
):
    integration_kwargs = (
        {"failed_request_status_codes": failed_request_status_codes}
        if failed_request_status_codes is not None
        else {}
    )
    sentry_init(
        integrations=[LitestarIntegration(**integration_kwargs)],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    @get("/error")
    async def error() -> None:
        raise HTTPException(status_code=status_code)

    app = Litestar([error])
    client = TestClient(app)

    if span_streaming:
        items = capture_items("event")

        client.get("/error")

        events = [item.payload for item in items]
    else:
        events = capture_events()

        client.get("/error")

    assert len(events) == int(expected_error)


@parametrize_test_configurable_status_codes
@pytest.mark.parametrize("span_streaming", [True, False])
def test_configurable_status_codes_middleware(
    sentry_init,
    capture_events,
    capture_items,
    failed_request_status_codes,
    status_code,
    expected_error,
    span_streaming,
):
    integration_kwargs = (
        {"failed_request_status_codes": failed_request_status_codes}
        if failed_request_status_codes is not None
        else {}
    )

    sentry_init(
        integrations=[LitestarIntegration(**integration_kwargs)],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    def create_raising_middleware(app):
        async def raising_middleware(scope, receive, send):
            raise HTTPException(status_code=status_code)

        return raising_middleware

    @get("/error")
    async def error() -> None: ...

    app = Litestar([error], middleware=[create_raising_middleware])
    client = TestClient(app)

    if span_streaming:
        items = capture_items("event")

        client.get("/error")

        events = [item.payload for item in items]
    else:
        events = capture_events()

        client.get("/error")

    assert len(events) == int(expected_error)


@pytest.mark.parametrize("span_streaming", [True, False])
def test_catch_non_http_exceptions_in_middleware(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[LitestarIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    def create_raising_middleware(app):
        async def raising_middleware(scope, receive, send):
            raise RuntimeError("Too Hot")

        return raising_middleware

    @get("/error")
    async def error() -> None: ...

    app = Litestar([error], middleware=[create_raising_middleware])
    client = TestClient(app)

    if span_streaming:
        items = capture_items("event")

        try:
            client.get("/error")
        except RuntimeError:
            pass

        events = [item.payload for item in items]
    else:
        events = capture_events()

        try:
            client.get("/error")
        except RuntimeError:
            pass

    assert len(events) == 1
    event_exception = events[0]["exception"]["values"][0]
    assert event_exception["type"] == "RuntimeError"
    assert event_exception["value"] == "Too Hot"

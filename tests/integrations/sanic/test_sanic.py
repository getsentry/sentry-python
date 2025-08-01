import asyncio
import contextlib
import os
import random
import sys
from typing import Any, Iterable, Optional, Container
from unittest.mock import Mock

import pytest

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.sanic import SanicIntegration
from sentry_sdk.tracing import TransactionSource

from sanic import Sanic, request, response, __version__ as SANIC_VERSION_RAW
from sanic.response import HTTPResponse
from sanic.exceptions import SanicException

try:
    from sanic_testing import TestManager
except ImportError:
    TestManager = None

try:
    from sanic_testing.reusable import ReusableClient
except ImportError:
    ReusableClient = None


SANIC_VERSION = tuple(map(int, SANIC_VERSION_RAW.split(".")))
PERFORMANCE_SUPPORTED = SANIC_VERSION >= (21, 9)


@pytest.fixture
def app():
    if SANIC_VERSION < (19,):
        """
        Older Sanic versions 0.8 and 18 bind to the same fixed port which
        creates problems when we run tests concurrently.
        """
        old_test_client = Sanic.test_client.__get__

        def new_test_client(self):
            client = old_test_client(self, Sanic)
            client.port += os.getpid() % 100
            return client

        Sanic.test_client = property(new_test_client)

    if SANIC_VERSION >= (20, 12) and SANIC_VERSION < (22, 6):
        # Some builds (20.12.0 intruduced and 22.6.0 removed again) have a feature where the instance is stored in an internal class
        # registry for later retrieval, and so add register=False to disable that
        sanic_app = Sanic("Test", register=False)
    else:
        sanic_app = Sanic("Test")

    if TestManager is not None:
        TestManager(sanic_app)

    @sanic_app.route("/message")
    def hi(request):
        capture_message("hi")
        return response.text("ok")

    @sanic_app.route("/message/<message_id>")
    def hi_with_id(request, message_id):
        capture_message("hi with id")
        return response.text("ok with id")

    @sanic_app.route("/500")
    def fivehundred(_):
        1 / 0

    return sanic_app


def get_client(app):
    @contextlib.contextmanager
    def simple_client(app):
        yield app.test_client

    if ReusableClient is not None:
        return ReusableClient(app)
    else:
        return simple_client(app)


def test_request_data(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    c = get_client(app)
    with c as client:
        _, response = client.get("/message?foo=bar")
        assert response.status == 200

    (event,) = events
    assert event["transaction"] == "hi"
    assert event["request"]["env"] == {"REMOTE_ADDR": ""}
    assert set(event["request"]["headers"]) >= {
        "accept",
        "accept-encoding",
        "host",
        "user-agent",
    }
    assert event["request"]["query_string"] == "foo=bar"
    assert event["request"]["url"].endswith("/message")
    assert event["request"]["method"] == "GET"

    # Assert that state is not leaked
    events.clear()
    capture_message("foo")
    (event,) = events

    assert "request" not in event
    assert "transaction" not in event


@pytest.mark.parametrize(
    "url,expected_transaction,expected_source",
    [
        ("/message", "hi", "component"),
        ("/message/123456", "hi_with_id", "component"),
    ],
)
def test_transaction_name(
    sentry_init, app, capture_events, url, expected_transaction, expected_source
):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    c = get_client(app)
    with c as client:
        _, response = client.get(url)
        assert response.status == 200

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


def test_errors(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    @app.route("/error")
    def myerror(request):
        raise ValueError("oh no")

    c = get_client(app)
    with c as client:
        _, response = client.get("/error")
        assert response.status == 500

    (event,) = events
    assert event["transaction"] == "myerror"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "ValueError"
    assert exception["value"] == "oh no"
    assert any(
        frame["filename"].endswith("test_sanic.py")
        for frame in exception["stacktrace"]["frames"]
    )


def test_bad_request_not_captured(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    @app.route("/")
    def index(request):
        raise SanicException("...", status_code=400)

    c = get_client(app)
    with c as client:
        _, response = client.get("/")
        assert response.status == 400

    assert not events


def test_error_in_errorhandler(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()])
    events = capture_events()

    @app.route("/error")
    def myerror(request):
        raise ValueError("oh no")

    @app.exception(ValueError)
    def myhandler(request, exception):
        1 / 0

    c = get_client(app)
    with c as client:
        _, response = client.get("/error")
        assert response.status == 500

    event1, event2 = events

    (exception,) = event1["exception"]["values"]
    assert exception["type"] == "ValueError"
    assert any(
        frame["filename"].endswith("test_sanic.py")
        for frame in exception["stacktrace"]["frames"]
    )

    exception = event2["exception"]["values"][-1]
    assert exception["type"] == "ZeroDivisionError"
    assert any(
        frame["filename"].endswith("test_sanic.py")
        for frame in exception["stacktrace"]["frames"]
    )


def test_concurrency(sentry_init, app):
    """
    Make sure we instrument Sanic in a way where request data does not leak
    between request handlers. This test also implicitly tests our concept of
    how async code should be instrumented, so if it breaks it likely has
    ramifications for other async integrations and async usercode.

    We directly call the request handler instead of using Sanic's test client
    because that's the only way we could reproduce leakage with such a low
    amount of concurrent tasks.
    """
    sentry_init(integrations=[SanicIntegration()])

    @app.route("/context-check/<i>")
    async def context_check(request, i):
        scope = sentry_sdk.get_isolation_scope()
        scope.set_tag("i", i)

        await asyncio.sleep(random.random())

        scope = sentry_sdk.get_isolation_scope()
        assert scope._tags["i"] == i

        return response.text("ok")

    async def task(i):
        responses = []

        kwargs = {
            "url_bytes": "http://localhost/context-check/{i}".format(i=i).encode(
                "ascii"
            ),
            "headers": {},
            "version": "1.1",
            "method": "GET",
            "transport": None,
        }

        if SANIC_VERSION >= (19,):
            kwargs["app"] = app

        if SANIC_VERSION >= (21, 3):

            class MockAsyncStreamer:
                def __init__(self, request_body):
                    self.request_body = request_body
                    self.iter = iter(self.request_body)

                    if SANIC_VERSION >= (21, 12):
                        self.response = None
                        self.stage = Mock()
                    else:
                        self.response = b"success"

                def respond(self, response):
                    responses.append(response)
                    patched_response = HTTPResponse()
                    return patched_response

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    try:
                        return next(self.iter)
                    except StopIteration:
                        raise StopAsyncIteration

            patched_request = request.Request(**kwargs)
            patched_request.stream = MockAsyncStreamer([b"hello", b"foo"])

            if SANIC_VERSION >= (21, 9):
                await app.dispatch(
                    "http.lifecycle.request",
                    context={"request": patched_request},
                    inline=True,
                )

            await app.handle_request(
                patched_request,
            )
        else:
            await app.handle_request(
                request.Request(**kwargs),
                write_callback=responses.append,
                stream_callback=responses.append,
            )

        (r,) = responses
        assert r.status == 200

    async def runner():
        if SANIC_VERSION >= (21, 3):
            if SANIC_VERSION >= (21, 9):
                await app._startup()
            else:
                try:
                    app.router.reset()
                    app.router.finalize()
                except AttributeError:
                    ...
        await asyncio.gather(*(task(i) for i in range(1000)))

    if sys.version_info < (3, 7):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner())
    else:
        asyncio.run(runner())

    scope = sentry_sdk.get_isolation_scope()
    assert not scope._tags


class TransactionTestConfig:
    """
    Data class to store configurations for each performance transaction test run, including
    both the inputs and relevant expected results.
    """

    def __init__(
        self,
        integration_args: Iterable[Optional[Container[int]]],
        url: str,
        expected_status: int,
        expected_transaction_name: Optional[str],
        expected_source: Optional[str] = None,
        has_transaction_event: bool = True,
    ) -> None:
        """
        expected_transaction_name of None indicates we expect to not receive a transaction
        """
        self.integration_args = integration_args
        self.url = url
        self.expected_status = expected_status
        self.expected_transaction_name = expected_transaction_name
        self.expected_source = expected_source
        self.has_transaction_event = has_transaction_event


@pytest.mark.skipif(
    not PERFORMANCE_SUPPORTED, reason="Performance not supported on this Sanic version"
)
@pytest.mark.parametrize(
    "test_config",
    [
        TransactionTestConfig(
            # Transaction for successful page load
            integration_args=(),
            url="/message",
            expected_status=200,
            expected_transaction_name="hi",
            expected_source=TransactionSource.COMPONENT,
        ),
        TransactionTestConfig(
            # Transaction still recorded when we have an internal server error
            integration_args=(),
            url="/500",
            expected_status=500,
            expected_transaction_name="fivehundred",
            expected_source=TransactionSource.COMPONENT,
        ),
        TransactionTestConfig(
            # By default, no transaction when we have a 404 error
            integration_args=(),
            url="/404",
            expected_status=404,
            expected_transaction_name=None,
            has_transaction_event=False,
        ),
        TransactionTestConfig(
            # With no ignored HTTP statuses, we should get transactions for 404 errors
            integration_args=(None,),
            url="/404",
            expected_status=404,
            expected_transaction_name="/404",
            expected_source=TransactionSource.URL,
        ),
        TransactionTestConfig(
            # Transaction can be suppressed for other HTTP statuses, too, by passing config to the integration
            integration_args=({200},),
            url="/message",
            expected_status=200,
            expected_transaction_name=None,
            has_transaction_event=False,
        ),
    ],
)
def test_transactions(
    test_config: TransactionTestConfig, sentry_init: Any, app: Any, capture_events: Any
) -> None:

    # Init the SanicIntegration with the desired arguments
    sentry_init(
        integrations=[SanicIntegration(*test_config.integration_args)],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Make request to the desired URL
    c = get_client(app)
    with c as client:
        _, response = client.get(test_config.url)
        assert response.status == test_config.expected_status

    # Extract the transaction events by inspecting the event types. We should at most have 1 transaction event.
    transaction_events = [
        e for e in events if "type" in e and e["type"] == "transaction"
    ]
    assert len(transaction_events) <= 1

    # Get the only transaction event, or set to None if there are no transaction events.
    (transaction_event, *_) = [*transaction_events, None]

    # We should have no transaction event if and only if we expect no transactions
    assert bool(transaction_event) == test_config.has_transaction_event

    # If a transaction was expected, ensure it is correct
    assert (
        transaction_event is None
        or transaction_event["transaction"] == test_config.expected_transaction_name
    )
    assert (
        transaction_event is None
        or transaction_event["transaction_info"]["source"]
        == test_config.expected_source
    )


@pytest.mark.skipif(
    not PERFORMANCE_SUPPORTED, reason="Performance not supported on this Sanic version"
)
def test_span_origin(sentry_init, app, capture_events):
    sentry_init(integrations=[SanicIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    c = get_client(app)
    with c as client:
        client.get("/message?foo=bar")

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.sanic"

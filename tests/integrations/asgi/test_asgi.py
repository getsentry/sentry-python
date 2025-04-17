from collections import Counter

import pytest
import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.integrations._asgi_common import _get_ip, _get_headers
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware, _looks_like_asgi3

from async_asgi_testclient import TestClient


@pytest.fixture
def asgi3_app():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif (
            scope["type"] == "http"
            and "route" in scope
            and scope["route"] == "/trigger/error"
        ):
            1 / 0

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                ],
            }
        )

        await send(
            {
                "type": "http.response.body",
                "body": b"Hello, world!",
            }
        )

    return app


@pytest.fixture
def asgi3_app_with_error():
    async def send_with_error(event):
        1 / 0

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    ...  # Do some startup here!
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    ...  # Do some shutdown here!
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        else:
            await send_with_error(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"text/plain"],
                    ],
                }
            )
            await send_with_error(
                {
                    "type": "http.response.body",
                    "body": b"Hello, world!",
                }
            )

    return app


@pytest.fixture
def asgi3_app_with_error_and_msg():
    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                ],
            }
        )

        capture_message("Let's try dividing by 0")
        1 / 0

        await send(
            {
                "type": "http.response.body",
                "body": b"Hello, world!",
            }
        )

    return app


@pytest.fixture
def asgi3_ws_app():
    def message():
        capture_message("Some message to the world!")
        raise ValueError("Oh no")

    async def app(scope, receive, send):
        await send(
            {
                "type": "websocket.send",
                "text": message(),
            }
        )

    return app


@pytest.fixture
def asgi3_custom_transaction_app():
    async def app(scope, receive, send):
        sentry_sdk.get_current_scope().set_transaction_name(
            "foobar", source=TransactionSource.CUSTOM
        )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                ],
            }
        )

        await send(
            {
                "type": "http.response.body",
                "body": b"Hello, world!",
            }
        )

    return app


def test_invalid_transaction_style(asgi3_app):
    with pytest.raises(ValueError) as exp:
        SentryAsgiMiddleware(asgi3_app, transaction_style="URL")

    assert (
        str(exp.value)
        == "Invalid value for transaction_style: URL (must be in ('endpoint', 'url'))"
    )


@pytest.mark.asyncio
async def test_capture_transaction(
    sentry_init,
    asgi3_app,
    capture_events,
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app)

    async with TestClient(app) as client:
        events = capture_events()
        await client.get("/some_url?somevalue=123")

    (transaction_event,) = events

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "/some_url"
    assert transaction_event["transaction_info"] == {"source": "url"}
    assert transaction_event["contexts"]["trace"]["op"] == "http.server"
    assert transaction_event["request"] == {
        "headers": {
            "host": "localhost",
            "remote-addr": "127.0.0.1",
            "user-agent": "ASGI-Test-Client",
        },
        "method": "GET",
        "query_string": "somevalue=123",
        "url": "http://localhost/some_url",
    }


@pytest.mark.asyncio
async def test_capture_transaction_with_error(
    sentry_init,
    asgi3_app_with_error,
    capture_events,
    DictionaryContaining,  # noqa: N803
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app_with_error)

    events = capture_events()
    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            await client.get("/some_url")

    (
        error_event,
        transaction_event,
    ) = events

    assert error_event["transaction"] == "/some_url"
    assert error_event["transaction_info"] == {"source": "url"}
    assert error_event["contexts"]["trace"]["op"] == "http.server"
    assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert error_event["exception"]["values"][0]["value"] == "division by zero"
    assert error_event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "asgi"

    assert transaction_event["type"] == "transaction"
    assert transaction_event["contexts"]["trace"] == DictionaryContaining(
        error_event["contexts"]["trace"]
    )
    assert transaction_event["contexts"]["trace"]["status"] == "internal_error"
    assert transaction_event["transaction"] == error_event["transaction"]
    assert transaction_event["request"] == error_event["request"]


@pytest.mark.asyncio
async def test_has_trace_if_performance_enabled(
    sentry_init,
    asgi3_app_with_error_and_msg,
    capture_events,
):
    sentry_init(traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get("/")

    msg_event, error_event, transaction_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert transaction_event["contexts"]["trace"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
        == msg_event["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.asyncio
async def test_has_trace_if_performance_disabled(
    sentry_init,
    asgi3_app_with_error_and_msg,
    capture_events,
):
    sentry_init()
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get("/")

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]


@pytest.mark.asyncio
async def test_trace_from_headers_if_performance_enabled(
    sentry_init,
    asgi3_app_with_error_and_msg,
    capture_events,
):
    sentry_init(traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)
    w3c_trace_header = "00-082b43a4192642f0b136d5159a501701-6e8f22c393e68f19-01"

    # If both sentry-trace and traceparent headers are present, sentry-trace takes precedence.
    # See: https://github.com/getsentry/team-sdks/issues/41

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get(
                "/",
                headers={
                    "sentry-trace": sentry_trace_header,
                    "traceparent": w3c_trace_header,
                },
            )

    msg_event, error_event, transaction_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert transaction_event["contexts"]["trace"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id
    assert transaction_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
async def test_trace_from_headers_if_performance_disabled(
    sentry_init,
    asgi3_app_with_error_and_msg,
    capture_events,
):
    sentry_init()
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)
    w3c_trace_header = "00-082b43a4192642f0b136d5159a501701-6e8f22c393e68f19-01"

    # If both sentry-trace and traceparent headers are present, sentry-trace takes precedence.
    # See: https://github.com/getsentry/team-sdks/issues/41

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get(
                "/",
                headers={
                    "sentry-trace": sentry_trace_header,
                    "traceparent": w3c_trace_header,
                },
            )

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]
    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
async def test_trace_from_w3c_headers_if_performance_enabled(
    sentry_init,
    asgi3_app_with_error_and_msg,
    capture_events,
):
    sentry_init(traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    trace_id = "582b43a4192642f0b136d5159a501701"
    w3c_trace_header = "00-{}-{}-{}".format(trace_id, "6e8f22c393e68f19", "01")

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get(
                "/",
                headers={
                    "traceparent": w3c_trace_header,
                },
            )

    msg_event, error_event, transaction_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert transaction_event["contexts"]["trace"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id
    assert transaction_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
async def test_trace_from_w3c_headers_if_performance_disabled(
    sentry_init,
    asgi3_app_with_error_and_msg,
    capture_events,
):
    sentry_init()
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    trace_id = "582b43a4192642f0b136d5159a501701"
    w3c_trace_header = "00-{}-{}-{}".format(trace_id, "6e8f22c393e68f19", "01")

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get(
                "/",
                headers={
                    "traceparent": w3c_trace_header,
                },
            )

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]
    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
async def test_websocket(sentry_init, asgi3_ws_app, capture_events, request):
    sentry_init(send_default_pii=True)

    events = capture_events()

    asgi3_ws_app = SentryAsgiMiddleware(asgi3_ws_app)

    scope = {
        "type": "websocket",
        "endpoint": asgi3_app,
        "client": ("127.0.0.1", 60457),
        "route": "some_url",
        "headers": [
            ("accept", "*/*"),
        ],
    }

    with pytest.raises(ValueError):
        async with TestClient(asgi3_ws_app, scope=scope) as client:
            async with client.websocket_connect("/ws") as ws:
                await ws.receive_text()

    msg_event, error_event = events

    assert msg_event["message"] == "Some message to the world!"

    (exc,) = error_event["exception"]["values"]
    assert exc["type"] == "ValueError"
    assert exc["value"] == "Oh no"


@pytest.mark.asyncio
async def test_auto_session_tracking_with_aggregates(
    sentry_init, asgi3_app, capture_envelopes
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app)

    scope = {
        "endpoint": asgi3_app,
        "client": ("127.0.0.1", 60457),
    }
    with pytest.raises(ZeroDivisionError):
        envelopes = capture_envelopes()
        async with TestClient(app, scope=scope) as client:
            scope["route"] = "/some/fine/url"
            await client.get("/some/fine/url")
            scope["route"] = "/some/fine/url"
            await client.get("/some/fine/url")
            scope["route"] = "/trigger/error"
            await client.get("/trigger/error")

    sentry_sdk.flush()

    count_item_types = Counter()
    for envelope in envelopes:
        count_item_types[envelope.items[0].type] += 1

    assert count_item_types["transaction"] == 3
    assert count_item_types["event"] == 1
    assert count_item_types["sessions"] == 1
    assert len(envelopes) == 5

    session_aggregates = envelopes[-1].items[0].payload.json["aggregates"]
    assert session_aggregates[0]["exited"] == 2
    assert session_aggregates[0]["crashed"] == 1
    assert len(session_aggregates) == 1


@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        (
            "/message",
            "url",
            "generic ASGI request",
            "route",
        ),
        (
            "/message",
            "endpoint",
            "tests.integrations.asgi.test_asgi.asgi3_app.<locals>.app",
            "component",
        ),
    ],
)
@pytest.mark.asyncio
async def test_transaction_style(
    sentry_init,
    asgi3_app,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    sentry_init(send_default_pii=True, traces_sample_rate=1.0)
    app = SentryAsgiMiddleware(asgi3_app, transaction_style=transaction_style)

    scope = {
        "endpoint": asgi3_app,
        "route": url,
        "client": ("127.0.0.1", 60457),
    }

    async with TestClient(app, scope=scope) as client:
        events = capture_events()
        await client.get(url)

    (transaction_event,) = events

    assert transaction_event["transaction"] == expected_transaction
    assert transaction_event["transaction_info"] == {"source": expected_source}


def mock_asgi2_app():
    pass


class MockAsgi2App:
    def __call__():
        pass


class MockAsgi3App(MockAsgi2App):
    def __await__():
        pass

    async def __call__():
        pass


def test_looks_like_asgi3(asgi3_app):
    # branch: inspect.isclass(app)
    assert _looks_like_asgi3(MockAsgi3App)
    assert not _looks_like_asgi3(MockAsgi2App)

    # branch: inspect.isfunction(app)
    assert _looks_like_asgi3(asgi3_app)
    assert not _looks_like_asgi3(mock_asgi2_app)

    # breanch: else
    asgi3 = MockAsgi3App()
    assert _looks_like_asgi3(asgi3)
    asgi2 = MockAsgi2App()
    assert not _looks_like_asgi3(asgi2)


def test_get_ip_x_forwarded_for():
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "8.8.8.8"

    # x-forwarded-for overrides x-real-ip
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "8.8.8.8"

    # when multiple x-forwarded-for headers are, the first is taken
    headers = [
        (b"x-forwarded-for", b"5.5.5.5"),
        (b"x-forwarded-for", b"6.6.6.6"),
        (b"x-forwarded-for", b"7.7.7.7"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "5.5.5.5"


def test_get_ip_x_real_ip():
    headers = [
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "10.10.10.10"

    # x-forwarded-for overrides x-real-ip
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "8.8.8.8"


def test_get_ip():
    # if now headers are provided the ip is taken from the client.
    headers = []
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "127.0.0.1"

    # x-forwarded-for header overides the ip from client
    headers = [
        (b"x-forwarded-for", b"8.8.8.8"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "8.8.8.8"

    # x-real-for header overides the ip from client
    headers = [
        (b"x-real-ip", b"10.10.10.10"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    ip = _get_ip(scope)
    assert ip == "10.10.10.10"


def test_get_headers():
    headers = [
        (b"x-real-ip", b"10.10.10.10"),
        (b"some_header", b"123"),
        (b"some_header", b"abc"),
    ]
    scope = {
        "client": ("127.0.0.1", 60457),
        "headers": headers,
    }
    headers = _get_headers(scope)
    assert headers == {
        "x-real-ip": "10.10.10.10",
        "some_header": "123, abc",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_url,transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "/message/123456",
            "url",
        ),
        (
            "/message/123456",
            "url",
            "/message/123456",
            "url",
        ),
    ],
)
async def test_transaction_name(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
    asgi3_app,
    capture_envelopes,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        traces_sample_rate=1.0,
    )

    envelopes = capture_envelopes()

    app = SentryAsgiMiddleware(asgi3_app, transaction_style=transaction_style)

    async with TestClient(app) as client:
        await client.get(request_url)

    (transaction_envelope,) = envelopes
    transaction_event = transaction_envelope.get_transaction_event()

    assert transaction_event["transaction"] == expected_transaction_name
    assert (
        transaction_event["transaction_info"]["source"] == expected_transaction_source
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_url, transaction_style,expected_transaction_name,expected_transaction_source",
    [
        (
            "/message/123456",
            "endpoint",
            "/message/123456",
            "url",
        ),
        (
            "/message/123456",
            "url",
            "/message/123456",
            "url",
        ),
    ],
)
async def test_transaction_name_in_traces_sampler(
    sentry_init,
    request_url,
    transaction_style,
    expected_transaction_name,
    expected_transaction_source,
    asgi3_app,
):
    """
    Tests that a custom traces_sampler has a meaningful transaction name.
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
        traces_sampler=dummy_traces_sampler,
        traces_sample_rate=1.0,
    )

    app = SentryAsgiMiddleware(asgi3_app, transaction_style=transaction_style)

    async with TestClient(app) as client:
        await client.get(request_url)


@pytest.mark.asyncio
async def test_custom_transaction_name(
    sentry_init, asgi3_custom_transaction_app, capture_events
):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()
    app = SentryAsgiMiddleware(asgi3_custom_transaction_app)

    async with TestClient(app) as client:
        await client.get("/test")

    (transaction_event,) = events
    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "foobar"
    assert transaction_event["transaction_info"] == {"source": "custom"}

from collections import Counter

import pytest
from async_asgi_testclient import TestClient

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations._asgi_common import (
    _get_headers,
    _get_ip,
)
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware, _looks_like_asgi3
from sentry_sdk.traces import SegmentNameSource
from tests.integrations.utils import DATA_COLLECTION_USER_INFO_CASES


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
            "foobar", source=SegmentNameSource.CUSTOM
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


@pytest.fixture
def asgi3_app_with_span():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

        with sentry_sdk.start_span(name="child-span"):
            pass

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [[b"content-type", b"text/plain"]],
            }
        )
        await send({"type": "http.response.body", "body": b"Hello, world!"})

    return app


@pytest.mark.asyncio
async def test_capture_transaction(
    sentry_init,
    asgi3_app,
    capture_items,
):
    sentry_init(
        data_collection={},
        traces_sample_rate=1.0,
    )
    app = SentryAsgiMiddleware(asgi3_app)

    async with TestClient(app) as client:
        items = capture_items("span")
        await client.get("/some_url?somevalue=123")

    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload

    assert span["is_segment"] is True
    assert span["name"] == "/some_url"

    assert span["attributes"]["sentry.segment.name.source"] == "url"
    assert span["attributes"]["sentry.op"] == "http.server"

    assert span["attributes"]["network.protocol.name"] == "http"
    assert span["attributes"]["http.request.method"] == "GET"
    assert span["attributes"]["http.request.header.host"] == ["localhost"]
    assert span["attributes"]["http.request.header.user-agent"] == ["ASGI-Test-Client"]
    assert span["attributes"]["url.full"] == "http://localhost/some_url?somevalue=123"


@pytest.mark.asyncio
async def test_capture_transaction_with_error(
    sentry_init,
    asgi3_app_with_error,
    capture_items,
):
    sentry_init(
        data_collection={},
        traces_sample_rate=1.0,
    )

    app = SentryAsgiMiddleware(asgi3_app_with_error)

    items = capture_items("event", "span")

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            await client.get("/some_url")

    sentry_sdk.flush()

    assert len(items) == 2
    assert items[0].type == "event"
    assert items[1].type == "span"

    error_event = items[0].payload
    span_item = items[1].payload

    assert error_event["transaction"] == "/some_url"
    assert error_event["transaction_info"] == {"source": "url"}
    assert error_event["contexts"]["trace"]["op"] == "http.server"
    assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert error_event["exception"]["values"][0]["value"] == "division by zero"
    assert error_event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "asgi"

    assert span_item["trace_id"] == error_event["contexts"]["trace"]["trace_id"]
    assert span_item["span_id"] == error_event["contexts"]["trace"]["span_id"]
    assert span_item.get("parent_span_id") == error_event["contexts"]["trace"].get(
        "parent_span_id"
    )
    assert span_item["status"] == "error"


@pytest.mark.asyncio
async def test_has_trace_if_performance_enabled(
    sentry_init,
    asgi3_app_with_error_and_msg,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
    )
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            items = capture_items("event", "span")
            await client.get("/")

    sentry_sdk.flush()

    msg_event, error_event, span = items

    assert msg_event.type == "event"
    msg_event = msg_event.payload
    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event.type == "event"
    error_event = error_event.payload
    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert span.type == "span"
    span = span.payload
    assert span["trace_id"] is not None

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == msg_event["contexts"]["trace"]["trace_id"]
        == span["trace_id"]
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
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
    )
    app = SentryAsgiMiddleware(asgi3_app_with_error_and_msg)

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            items = capture_items("event", "span")
            await client.get("/", headers={"sentry-trace": sentry_trace_header})

    sentry_sdk.flush()

    msg_event, error_event, span = items

    assert msg_event.type == "event"
    msg_event = msg_event.payload
    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event.type == "event"
    error_event = error_event.payload
    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert span.type == "span"
    span = span.payload
    assert span["trace_id"] is not None

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id
    assert span["trace_id"] == trace_id


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

    with pytest.raises(ZeroDivisionError):
        async with TestClient(app) as client:
            events = capture_events()
            await client.get("/", headers={"sentry-trace": sentry_trace_header})

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]
    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
async def test_websocket(
    sentry_init,
    asgi3_ws_app,
    capture_items,
):
    sentry_init(
        data_collection={},
        traces_sample_rate=1.0,
    )

    asgi3_ws_app = SentryAsgiMiddleware(asgi3_ws_app)

    request_url = "/ws"

    with pytest.raises(ValueError):
        client = TestClient(asgi3_ws_app)
        items = capture_items("event", "span")
        async with client.websocket_connect(request_url) as ws:
            await ws.receive_text()

    sentry_sdk.flush()

    msg_event, error_event, span = items

    assert msg_event.type == "event"
    msg_event = msg_event.payload
    assert msg_event["transaction"] == request_url
    assert msg_event["transaction_info"] == {"source": "url"}
    assert msg_event["message"] == "Some message to the world!"

    assert error_event.type == "event"
    error_event = error_event.payload
    (exc,) = error_event["exception"]["values"]
    assert exc["type"] == "ValueError"
    assert exc["value"] == "Oh no"

    assert span.type == "span"
    span = span.payload
    assert span["name"] == request_url
    assert span["attributes"]["sentry.segment.name.source"] == "url"


@pytest.mark.asyncio
async def test_auto_session_tracking_with_aggregates(
    sentry_init, asgi3_app, capture_envelopes
):
    sentry_init(
        data_collection={},
        traces_sample_rate=1.0,
    )
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

    assert count_item_types["span"] == 3
    assert count_item_types["event"] == 1
    assert count_item_types["sessions"] == 1
    assert len(envelopes) == 5

    (session,) = [
        envelope for envelope in envelopes if envelope.items[0].type == "sessions"
    ]
    session_aggregates = session.items[0].payload.json["aggregates"]
    assert session_aggregates[0]["exited"] == 2
    assert session_aggregates[0]["crashed"] == 1
    assert len(session_aggregates) == 1


@pytest.mark.asyncio
async def test_fallback_segment_name_and_source(
    sentry_init,
    asgi3_app,
    capture_items,
):
    sentry_init(
        data_collection={},
        traces_sample_rate=1.0,
    )
    app = SentryAsgiMiddleware(asgi3_app)

    scope = {
        "endpoint": asgi3_app,
        "route": "/message",
        "client": ("127.0.0.1", 60457),
    }

    async with TestClient(app, scope=scope) as client:
        items = capture_items("span")
        await client.get("/message")

    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload

    assert span["name"] == "generic ASGI request"
    assert span["attributes"]["sentry.segment.name.source"] == "route"


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
    "data_collection, expected_headers",
    [
        pytest.param(
            {},
            {
                "http.request.header.authorization": ["[Filtered]"],
                "http.request.header.x-custom-header": ["passthrough"],
            },
            id="default_redacts_sensitive_headers",
        ),
        pytest.param(
            {"http_headers": {"request": {"mode": "off"}}},
            None,
            id="mode_off_collects_no_headers",
        ),
        pytest.param(
            {"http_headers": {"request": {"mode": "allowlist", "terms": ["custom"]}}},
            {
                "http.request.header.x-custom-header": ["passthrough"],
                "http.request.header.x-forwarded-for": ["[Filtered]"],
                "http.request.header.host": ["[Filtered]"],
            },
            id="allowlist_redacts_all_but_allowed_terms",
        ),
        pytest.param(
            {"http_headers": {"request": {"mode": "denylist", "terms": ["custom"]}}},
            {
                "http.request.header.x-custom-header": ["[Filtered]"],
                "http.request.header.x-forwarded-for": ["1.2.3.4"],
                "http.request.header.host": ["localhost"],
            },
            id="denylist_redacts_only_matched_terms",
        ),
    ],
)
async def test_request_headers_data_collection(
    sentry_init, asgi3_app, capture_items, data_collection, expected_headers
):
    sentry_init(
        traces_sample_rate=1.0,
        data_collection=data_collection,
    )
    app = SentryAsgiMiddleware(asgi3_app)

    items = capture_items("span")
    async with TestClient(app) as client:
        await client.get(
            "/some_url",
            headers={
                "Authorization": "Bearer secret-token",
                "X-Forwarded-For": "1.2.3.4",
                "X-Custom-Header": "passthrough",
            },
        )

    sentry_sdk.flush()

    (span,) = [item.payload for item in items]
    attributes = span["attributes"]

    if expected_headers is None:
        assert not any(key.startswith("http.request.header.") for key in attributes)
    else:
        for key, value in expected_headers.items():
            assert attributes[key] == value


@pytest.mark.asyncio
async def test_request_headers_data_collection_cookie_always_redacted(
    sentry_init, asgi3_app, capture_items
):
    sentry_init(
        traces_sample_rate=1.0,
        data_collection={
            "http_headers": {
                "request": {"mode": "allowlist", "terms": ["cookie", "custom"]}
            }
        },
    )
    app = SentryAsgiMiddleware(asgi3_app)

    items = capture_items("span")
    async with TestClient(app) as client:
        await client.get(
            "/some_url",
            headers={
                "Cookie": "sessionid=secret",
                "X-Custom-Header": "passthrough",
            },
        )

    sentry_sdk.flush()

    (span,) = [item.payload for item in items]
    attributes = span["attributes"]

    assert attributes["http.request.header.cookie"] == ["[Filtered]"]
    assert attributes["http.request.header.x-custom-header"] == ["passthrough"]


@pytest.mark.asyncio
async def test_get_request_attributes_url_with_filtered_host(
    sentry_init, capture_items, asgi3_app
):
    # As with the request data, an allowlist mode that does not allow "host" scrubs
    # the host header value, but "url.full" must still resolve rather than embedding
    # the substituted value.
    sentry_init(
        traces_sample_rate=1.0,
        data_collection={
            "http_headers": {"request": {"mode": "allowlist", "terms": []}}
        },
    )
    app = SentryAsgiMiddleware(asgi3_app)

    items = capture_items("span")
    scope = {"server": ("example.com", 80), "scheme": "http"}
    async with TestClient(app, scope=scope) as client:
        await client.get("/foo?somevalue=123", headers={"host": "example.com"})

    sentry_sdk.flush()

    assert len(items) == 1
    attributes = items[0].payload["attributes"]

    assert attributes["http.request.header.host"] == ["[Filtered]"]
    assert attributes["url.full"] == "http://example.com/foo?somevalue=123"


@pytest.mark.asyncio
async def test_get_request_attributes_url_with_headers_off(
    sentry_init, capture_items, asgi3_app
):
    # "off" mode in data collection captures no headers at all, but "url.full" must
    # still resolve via the (uncaptured) host header rather than being dropped.
    sentry_init(
        traces_sample_rate=1.0,
        data_collection={"http_headers": {"request": {"mode": "off"}}},
    )
    app = SentryAsgiMiddleware(asgi3_app)

    items = capture_items("span")
    scope = {"server": ("example.com", 80), "scheme": "http"}
    async with TestClient(app, scope=scope) as client:
        await client.get("/foo?somevalue=123", headers={"host": "example.com"})

    sentry_sdk.flush()

    assert len(items) == 1
    attributes = items[0].payload["attributes"]

    assert not any(key.startswith("http.request.header.") for key in attributes)
    assert attributes["url.full"] == "http://example.com/foo?somevalue=123"


QUERY_STRING = "token=abc&theme=dark&lang=en&session=xyz"


def _http_scope():
    return {"server": ("example.com", 80), "scheme": "http"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, request_url, expected_query, expected_url_full",
    [
        pytest.param(
            {"data_collection": {}},
            "/foo?" + QUERY_STRING,
            "token=%5BFiltered%5D&theme=dark&lang=en&session=%5BFiltered%5D",
            "http://example.com/foo?token=%5BFiltered%5D&theme=dark&lang=en&session=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "data_collection": {
                    "url_query_params": {"mode": "allowlist", "terms": ["theme"]}
                }
            },
            "/foo?" + QUERY_STRING,
            "token=%5BFiltered%5D&theme=dark&lang=%5BFiltered%5D&session=%5BFiltered%5D",
            "http://example.com/foo?token=%5BFiltered%5D&theme=dark&lang=%5BFiltered%5D&session=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {"data_collection": {"url_query_params": {"mode": "off"}}},
            "/foo?" + QUERY_STRING,
            None,
            "http://example.com/foo",
            id="data_collection_off",
        ),
        pytest.param(
            {"data_collection": {}},
            "/foo",
            None,
            "http://example.com/foo",
            id="empty_query_string",
        ),
    ],
)
async def test_get_request_attributes_query_data_collection(
    sentry_init,
    capture_items,
    asgi3_app,
    init_kwargs,
    request_url,
    expected_query,
    expected_url_full,
):
    sentry_init(
        traces_sample_rate=1.0,
        **init_kwargs,
    )
    app = SentryAsgiMiddleware(asgi3_app)

    items = capture_items("span")
    async with TestClient(app, scope=_http_scope()) as client:
        await client.get(request_url, headers={"host": "example.com"})

    sentry_sdk.flush()

    assert len(items) == 1
    attributes = items[0].payload["attributes"]

    if expected_query is None:
        assert "http.query" not in attributes
    else:
        assert attributes["http.query"] == expected_query

    if expected_url_full is None:
        assert "url.full" not in attributes
        assert "url.path" not in attributes
    else:
        assert attributes["url.full"] == expected_url_full
        assert attributes["url.path"] == "/foo"


USER_INFO_CASES = [
    pytest.param(
        {"data_collection": {"user_info": False}},
        True,
        False,
        id="dc_user_info_false",
    ),
    pytest.param(
        {"data_collection": {}},
        True,
        True,
        id="dc_default_user_info",
    ),
    pytest.param(
        {"data_collection": {}},
        False,
        False,
        id="no_client",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("init_kwargs, has_client, expect_ip", USER_INFO_CASES)
async def test_get_request_attributes_client_address_user_info(
    sentry_init, capture_items, asgi3_app, init_kwargs, has_client, expect_ip
):
    sentry_init(
        traces_sample_rate=1.0,
        **init_kwargs,
    )
    app = SentryAsgiMiddleware(asgi3_app)

    scope = _http_scope()
    if has_client:
        scope["client"] = ("127.0.0.1", 60457)

    items = capture_items("span")
    async with TestClient(app, scope=scope) as client:
        await client.get("/foo", headers={"host": "example.com"})

    sentry_sdk.flush()

    assert len(items) == 1
    attributes = items[0].payload["attributes"]

    if expect_ip:
        assert attributes["client.address"] == "127.0.0.1"
    else:
        assert "client.address" not in attributes


@pytest.mark.asyncio
async def test_segment_name_and_source(
    sentry_init,
    asgi3_app,
    capture_items,
):
    """
    Tests that the transaction name is something meaningful.
    """
    sentry_init(
        traces_sample_rate=1.0,
    )

    items = capture_items("span")

    app = SentryAsgiMiddleware(asgi3_app)

    async with TestClient(app) as client:
        await client.get("/message/123456")

    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload

    assert span["name"] == "/message/123456"
    assert span["attributes"]["sentry.segment.name.source"] == "url"


@pytest.mark.asyncio
async def test_transaction_name_in_traces_sampler(
    sentry_init,
    asgi3_app,
):
    """
    Tests that a custom traces_sampler has a meaningful transaction name.
    In this case the URL or endpoint, because we do not have the route yet.
    """

    def dummy_traces_sampler(sampling_context):
        assert sampling_context["transaction_context"]["name"] == "/message/123456"
        assert sampling_context["transaction_context"]["source"] == "url"

    sentry_init(
        traces_sampler=dummy_traces_sampler,
        traces_sample_rate=1.0,
    )

    app = SentryAsgiMiddleware(asgi3_app)

    async with TestClient(app) as client:
        await client.get("/message/123456")


@pytest.mark.asyncio
async def test_custom_transaction_name(
    sentry_init,
    asgi3_custom_transaction_app,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
    )
    app = SentryAsgiMiddleware(asgi3_custom_transaction_app)

    async with TestClient(app) as client:
        items = capture_items("span")
        await client.get("/test")

    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload

    assert span["is_segment"] is True
    assert span["name"] == "foobar"
    assert span["attributes"]["sentry.segment.name.source"] == "custom"


@pytest.mark.asyncio
@pytest.mark.parametrize("data_collection, expect_ip", DATA_COLLECTION_USER_INFO_CASES)
async def test_user_ip_address_on_all_spans(
    sentry_init,
    capture_items,
    data_collection,
    expect_ip,
    asgi3_app_with_span,
):
    sentry_init(
        traces_sample_rate=1.0,
        data_collection=data_collection,
    )

    app = SentryAsgiMiddleware(asgi3_app_with_span)

    async def wrapped_app(scope, receive, send):
        scope["client"] = ("127.0.0.1", 0)
        await app(scope, receive, send)

    async with TestClient(wrapped_app) as client:
        items = capture_items("span")
        await client.get("/some_url")

    sentry_sdk.flush()

    child_span, server_span = [item.payload for item in items]

    if expect_ip:
        assert server_span["attributes"]["user.ip_address"] == "127.0.0.1"
        assert child_span["attributes"]["user.ip_address"] == "127.0.0.1"
    else:
        assert "user.ip_address" not in server_span["attributes"]
        assert "user.ip_address" not in child_span["attributes"]


@pytest.mark.parametrize(
    "client_ip, server, host_header, is_localhost",
    [
        # Loopback IP
        ("127.0.0.1", ("example.com", 80), b"example.com", True),
        # IPv6 loopback
        ("::1", ("example.com", 80), b"example.com", True),
        # Localhost host header with non-local IP
        ("203.0.113.50", ("example.com", 80), b"localhost:8000", True),
        # Server bound to localhost, but host header is public (reverse proxy)
        ("203.0.113.50", ("localhost", 8000), b"example.com", False),
        # Server bound to localhost, no host header (fallback to server)
        ("203.0.113.50", ("localhost", 8000), None, True),
        # Non-local everything
        ("203.0.113.50", ("example.com", 80), b"example.com", False),
    ],
)
@pytest.mark.asyncio
async def test_is_localhost_attribute(
    sentry_init,
    capture_items,
    client_ip,
    server,
    host_header,
    is_localhost,
    asgi3_app_with_span,
):
    sentry_init(
        traces_sample_rate=1.0,
    )

    app = SentryAsgiMiddleware(asgi3_app_with_span)

    async def wrapped_app(scope, receive, send):
        if scope["type"] != "lifespan":
            scope["client"] = (client_ip, 0)
            scope["server"] = server
            scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"host"]
            if host_header is not None:
                scope["headers"].append((b"host", host_header))

        await app(scope, receive, send)

    async with TestClient(wrapped_app) as client:
        items = capture_items("span")
        await client.get("/some_url")

    sentry_sdk.flush()

    child_span, server_span = [item.payload for item in items]

    assert server_span["attributes"]["sentry.is_localhost"] is is_localhost
    assert child_span["attributes"]["sentry.is_localhost"] is is_localhost


@pytest.mark.asyncio
async def test_user_agent_original_attribute(
    sentry_init,
    capture_items,
    asgi3_app_with_span,
):
    sentry_init(
        traces_sample_rate=1.0,
    )

    app = SentryAsgiMiddleware(asgi3_app_with_span)

    async with TestClient(app) as client:
        items = capture_items("span")
        await client.get("/some_url", headers={"User-Agent": "TestBrowser/1.0"})

    sentry_sdk.flush()

    child_span, server_span = [item.payload for item in items]

    assert server_span["attributes"]["user_agent.original"] == "TestBrowser/1.0"
    assert child_span["attributes"]["user_agent.original"] == "TestBrowser/1.0"

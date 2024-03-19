import asyncio
import json
from contextlib import suppress

import pytest
from aiohttp import web
from aiohttp.client import ServerDisconnectedError
from aiohttp.web_request import Request

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from tests.conftest import ApproxDict

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.mark.asyncio
async def test_basic(sentry_init, aiohttp_client, capture_events):
    sentry_init(integrations=[AioHttpIntegration()])

    async def hello(request):
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 500

    (event,) = events

    assert (
        event["transaction"]
        == "tests.integrations.aiohttp.test_aiohttp.test_basic.<locals>.hello"
    )

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    request = event["request"]
    host = request["headers"]["Host"]

    assert request["env"] == {"REMOTE_ADDR": "127.0.0.1"}
    assert request["method"] == "GET"
    assert request["query_string"] == ""
    assert request.get("data") is None
    assert request["url"] == "http://{host}/".format(host=host)
    assert request["headers"] == {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Host": host,
        "User-Agent": request["headers"]["User-Agent"],
        "baggage": mock.ANY,
        "sentry-trace": mock.ANY,
    }


@pytest.mark.asyncio
async def test_post_body_not_read(sentry_init, aiohttp_client, capture_events):
    from sentry_sdk.integrations.aiohttp import BODY_NOT_READ_MESSAGE

    sentry_init(integrations=[AioHttpIntegration()])

    body = {"some": "value"}

    async def hello(request):
        1 / 0

    app = web.Application()
    app.router.add_post("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.post("/", json=body)
    assert resp.status == 500

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    request = event["request"]

    assert request["env"] == {"REMOTE_ADDR": "127.0.0.1"}
    assert request["method"] == "POST"
    assert request["data"] == BODY_NOT_READ_MESSAGE


@pytest.mark.asyncio
async def test_post_body_read(sentry_init, aiohttp_client, capture_events):
    sentry_init(integrations=[AioHttpIntegration()])

    body = {"some": "value"}

    async def hello(request):
        await request.json()
        1 / 0

    app = web.Application()
    app.router.add_post("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.post("/", json=body)
    assert resp.status == 500

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    request = event["request"]

    assert request["env"] == {"REMOTE_ADDR": "127.0.0.1"}
    assert request["method"] == "POST"
    assert request["data"] == json.dumps(body)


@pytest.mark.asyncio
async def test_403_not_captured(sentry_init, aiohttp_client, capture_events):
    sentry_init(integrations=[AioHttpIntegration()])

    async def hello(request):
        raise web.HTTPForbidden()

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 403

    assert not events


@pytest.mark.asyncio
async def test_cancelled_error_not_captured(
    sentry_init, aiohttp_client, capture_events
):
    sentry_init(integrations=[AioHttpIntegration()])

    async def hello(request):
        raise asyncio.CancelledError()

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()
    client = await aiohttp_client(app)

    with suppress(ServerDisconnectedError):
        # Intended `aiohttp` interaction: server will disconnect if it
        # encounters `asyncio.CancelledError`
        await client.get("/")

    assert not events


@pytest.mark.asyncio
async def test_half_initialized(sentry_init, aiohttp_client, capture_events):
    sentry_init(integrations=[AioHttpIntegration()])
    sentry_init()

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 200

    assert events == []


@pytest.mark.asyncio
async def test_tracing(sentry_init, aiohttp_client, capture_events):
    sentry_init(integrations=[AioHttpIntegration()], traces_sample_rate=1.0)

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 200

    (event,) = events

    assert event["type"] == "transaction"
    assert (
        event["transaction"]
        == "tests.integrations.aiohttp.test_aiohttp.test_tracing.<locals>.hello"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        (
            "/message",
            "handler_name",
            "tests.integrations.aiohttp.test_aiohttp.test_transaction_style.<locals>.hello",
            "component",
        ),
        (
            "/message",
            "method_and_path_pattern",
            "GET /{var}",
            "route",
        ),
    ],
)
async def test_transaction_style(
    sentry_init,
    aiohttp_client,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    sentry_init(
        integrations=[AioHttpIntegration(transaction_style=transaction_style)],
        traces_sample_rate=1.0,
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/{var}", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get(url)
    assert resp.status == 200

    (event,) = events

    assert event["type"] == "transaction"
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


@pytest.mark.tests_internal_exceptions
@pytest.mark.asyncio
async def test_tracing_unparseable_url(sentry_init, aiohttp_client, capture_events):
    sentry_init(integrations=[AioHttpIntegration()], traces_sample_rate=1.0)

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    with mock.patch(
        "sentry_sdk.integrations.aiohttp.parse_url", side_effect=ValueError
    ):
        resp = await client.get("/")

    assert resp.status == 200

    (event,) = events

    assert event["type"] == "transaction"
    assert (
        event["transaction"]
        == "tests.integrations.aiohttp.test_aiohttp.test_tracing_unparseable_url.<locals>.hello"
    )


@pytest.mark.asyncio
async def test_traces_sampler_gets_request_object_in_sampling_context(
    sentry_init,
    aiohttp_client,
    DictionaryContaining,  # noqa:N803
    ObjectDescribedBy,
):
    traces_sampler = mock.Mock()
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sampler=traces_sampler,
    )

    async def kangaroo_handler(request):
        return web.Response(text="dogs are great")

    app = web.Application()
    app.router.add_get("/tricks/kangaroo", kangaroo_handler)

    client = await aiohttp_client(app)
    await client.get("/tricks/kangaroo")

    traces_sampler.assert_any_call(
        DictionaryContaining(
            {
                "aiohttp_request": ObjectDescribedBy(
                    type=Request, attrs={"method": "GET", "path": "/tricks/kangaroo"}
                )
            }
        )
    )


@pytest.mark.asyncio
async def test_has_trace_if_performance_enabled(
    sentry_init, aiohttp_client, capture_events
):
    sentry_init(integrations=[AioHttpIntegration()], traces_sample_rate=1.0)

    async def hello(request):
        capture_message("It's a good day to try dividing by 0")
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 500

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
    sentry_init, aiohttp_client, capture_events
):
    sentry_init(integrations=[AioHttpIntegration()])

    async def hello(request):
        capture_message("It's a good day to try dividing by 0")
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 500

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == msg_event["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.asyncio
async def test_trace_from_headers_if_performance_enabled(
    sentry_init, aiohttp_client, capture_events
):
    sentry_init(integrations=[AioHttpIntegration()], traces_sample_rate=1.0)

    async def hello(request):
        capture_message("It's a good day to try dividing by 0")
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    # The aiohttp_client is instrumented so will generate the sentry-trace header and add request.
    # Get the sentry-trace header from the request so we can later compare with transaction events.
    client = await aiohttp_client(app)
    resp = await client.get("/")
    sentry_trace_header = resp.request_info.headers.get("sentry-trace")
    trace_id = sentry_trace_header.split("-")[0]

    assert resp.status == 500

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
    sentry_init, aiohttp_client, capture_events
):
    sentry_init(integrations=[AioHttpIntegration()])

    async def hello(request):
        capture_message("It's a good day to try dividing by 0")
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    # The aiohttp_client is instrumented so will generate the sentry-trace header and add request.
    # Get the sentry-trace header from the request so we can later compare with transaction events.
    client = await aiohttp_client(app)
    resp = await client.get("/")
    sentry_trace_header = resp.request_info.headers.get("sentry-trace")
    trace_id = sentry_trace_header.split("-")[0]

    assert resp.status == 500

    msg_event, error_event = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
async def test_crumb_capture(
    sentry_init, aiohttp_raw_server, aiohttp_client, loop, capture_events
):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(
        integrations=[AioHttpIntegration()], before_breadcrumb=before_breadcrumb
    )

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    with start_transaction():
        events = capture_events()

        client = await aiohttp_client(raw_server)
        resp = await client.get("/")
        assert resp.status == 200
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        assert crumb["category"] == "httplib"
        assert crumb["data"] == ApproxDict(
            {
                "url": "http://127.0.0.1:{}/".format(raw_server.port),
                "http.fragment": "",
                "http.method": "GET",
                "http.query": "",
                "http.response.status_code": 200,
                "reason": "OK",
                "extra": "foo",
            }
        )


@pytest.mark.asyncio
async def test_outgoing_trace_headers(sentry_init, aiohttp_raw_server, aiohttp_client):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
    )

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    with start_transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        # make trace_id difference between transactions
        trace_id="0123456789012345678901234567890",
    ) as transaction:
        client = await aiohttp_client(raw_server)
        resp = await client.get("/")
        request_span = transaction._span_recorder.spans[-1]

        assert resp.request_info.headers[
            "sentry-trace"
        ] == "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=request_span.span_id,
            sampled=1,
        )


@pytest.mark.asyncio
async def test_outgoing_trace_headers_append_to_baggage(
    sentry_init, aiohttp_raw_server, aiohttp_client
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    with start_transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="0123456789012345678901234567890",
    ):
        client = await aiohttp_client(raw_server)
        resp = await client.get("/", headers={"bagGage": "custom=value"})

        assert (
            resp.request_info.headers["baggage"]
            == "custom=value,sentry-trace_id=0123456789012345678901234567890,sentry-environment=production,sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,sentry-transaction=/interactions/other-dogs/new-dog,sentry-sample_rate=1.0,sentry-sampled=true"
        )

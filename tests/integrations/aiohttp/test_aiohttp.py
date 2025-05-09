import asyncio
import json
import re
from contextlib import suppress
from unittest import mock

import pytest

try:
    import pytest_asyncio
except ImportError:
    pytest_asyncio = None

from aiohttp import web, ClientSession
from aiohttp.client import ServerDisconnectedError
from aiohttp.web_exceptions import (
    HTTPInternalServerError,
    HTTPNetworkAuthenticationRequired,
    HTTPBadRequest,
    HTTPNotFound,
    HTTPUnavailableForLegalReasons,
)

from sentry_sdk import capture_message, start_span
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from tests.conftest import ApproxDict


if pytest_asyncio is None:
    # `loop` was deprecated in `pytest-aiohttp`
    # in favor of `event_loop` from `pytest-asyncio`
    @pytest.fixture
    def event_loop(loop):
        yield loop


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
        "Accept-Encoding": mock.ANY,
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
async def test_traces_sampler_gets_attributes_in_sampling_context(
    sentry_init,
    aiohttp_client,
):
    traces_sampler = mock.Mock(return_value=True)

    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sampler=traces_sampler,
    )

    async def kangaroo_handler(request):
        return web.Response(text="dogs are great")

    app = web.Application()
    app.router.add_get("/tricks/kangaroo", kangaroo_handler)

    client = await aiohttp_client(app)
    await client.get(
        "/tricks/kangaroo?jump=high", headers={"Custom-Header": "Custom Value"}
    )

    assert traces_sampler.call_count == 1
    sampling_context = traces_sampler.call_args_list[0][0][0]
    assert isinstance(sampling_context, dict)
    assert re.match(
        r"http:\/\/127\.0\.0\.1:[0-9]{4,5}\/tricks\/kangaroo\?jump=high",
        sampling_context["url.full"],
    )
    assert sampling_context["url.path"] == "/tricks/kangaroo"
    assert sampling_context["url.query"] == "jump=high"
    assert sampling_context["url.scheme"] == "http"
    assert sampling_context["http.request.method"] == "GET"
    assert sampling_context["server.address"] == "127.0.0.1"
    assert sampling_context["server.port"].isnumeric()
    assert sampling_context["http.request.header.custom-header"] == "Custom Value"


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
    with start_span(name="request"):
        # Headers are only added to the span if there is an active transaction
        resp = await client.get("/")

    sentry_trace_header = resp.request_info.headers.get("sentry-trace")
    trace_id = sentry_trace_header.split("-")[0]

    assert resp.status == 500

    # Last item is the custom transaction event wrapping `client.get("/")`
    msg_event, error_event, transaction_event, _ = events

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
    sentry_init, aiohttp_raw_server, aiohttp_client, event_loop, capture_events
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

    with start_span(name="breadcrumb"):
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


@pytest.mark.parametrize(
    "status_code,level",
    [
        (200, "info"),
        (301, "info"),
        (403, "warning"),
        (405, "warning"),
        (500, "error"),
    ],
)
@pytest.mark.asyncio
async def test_crumb_capture_client_error(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    event_loop,
    capture_events,
    status_code,
    level,
):
    sentry_init(integrations=[AioHttpIntegration()])

    async def handler(request):
        return web.Response(status=status_code)

    raw_server = await aiohttp_raw_server(handler)

    with start_span(name="crumbs"):
        events = capture_events()

        client = await aiohttp_client(raw_server)
        resp = await client.get("/")
        assert resp.status == status_code
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        assert crumb["level"] == level
        assert crumb["category"] == "httplib"
        assert crumb["data"] == ApproxDict(
            {
                "url": "http://127.0.0.1:{}/".format(raw_server.port),
                "http.fragment": "",
                "http.method": "GET",
                "http.query": "",
                "http.response.status_code": status_code,
            }
        )


@pytest.mark.asyncio
async def test_outgoing_trace_headers(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_envelopes
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
    )

    envelopes = capture_envelopes()

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    with start_span(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
    ) as transaction:
        client = await aiohttp_client(raw_server)
        resp = await client.get("/")

    (envelope,) = envelopes
    transaction = envelope.get_transaction_event()
    request_span = transaction["spans"][-1]

    assert resp.request_info.headers[
        "sentry-trace"
    ] == "{trace_id}-{parent_span_id}-{sampled}".format(
        trace_id=transaction["contexts"]["trace"]["trace_id"],
        parent_span_id=request_span["span_id"],
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

    with mock.patch("sentry_sdk.tracing_utils.Random.uniform", return_value=0.5):
        with start_span(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
        ) as transaction:
            client = await aiohttp_client(raw_server)
            resp = await client.get("/", headers={"bagGage": "custom=value"})

            assert sorted(resp.request_info.headers["baggage"].split(",")) == sorted(
                [
                    "custom=value",
                    f"sentry-trace_id={transaction.trace_id}",
                    "sentry-environment=production",
                    "sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42",
                    "sentry-transaction=/interactions/other-dogs/new-dog",
                    "sentry-sample_rate=1.0",
                    "sentry-sampled=true",
                    "sentry-sample_rand=0.500000",
                ]
            )


@pytest.mark.asyncio
async def test_span_origin(
    sentry_init,
    aiohttp_client,
    capture_events,
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
    )

    async def hello(request):
        async with ClientSession() as session:
            async with session.get("http://example.com"):
                return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    await client.get("/")

    (event,) = events
    assert event["contexts"]["trace"]["origin"] == "auto.http.aiohttp"
    assert event["spans"][0]["origin"] == "auto.http.aiohttp"


@pytest.mark.parametrize(
    ("integration_kwargs", "exception_to_raise", "should_capture"),
    (
        ({}, None, False),
        ({}, HTTPBadRequest, False),
        (
            {},
            HTTPUnavailableForLegalReasons(None),
            False,
        ),  # Highest 4xx status code (451)
        ({}, HTTPInternalServerError, True),
        ({}, HTTPNetworkAuthenticationRequired, True),  # Highest 5xx status code (511)
        ({"failed_request_status_codes": set()}, HTTPInternalServerError, False),
        (
            {"failed_request_status_codes": set()},
            HTTPNetworkAuthenticationRequired,
            False,
        ),
        ({"failed_request_status_codes": {404, *range(500, 600)}}, HTTPNotFound, True),
        (
            {"failed_request_status_codes": {404, *range(500, 600)}},
            HTTPInternalServerError,
            True,
        ),
        (
            {"failed_request_status_codes": {404, *range(500, 600)}},
            HTTPBadRequest,
            False,
        ),
    ),
)
@pytest.mark.asyncio
async def test_failed_request_status_codes(
    sentry_init,
    aiohttp_client,
    capture_events,
    integration_kwargs,
    exception_to_raise,
    should_capture,
):
    sentry_init(integrations=[AioHttpIntegration(**integration_kwargs)])
    events = capture_events()

    async def handle(_):
        if exception_to_raise is not None:
            raise exception_to_raise
        else:
            return web.Response(status=200)

    app = web.Application()
    app.router.add_get("/", handle)

    client = await aiohttp_client(app)
    resp = await client.get("/")

    expected_status = (
        200 if exception_to_raise is None else exception_to_raise.status_code
    )
    assert resp.status == expected_status

    if should_capture:
        (event,) = events
        assert event["exception"]["values"][0]["type"] == exception_to_raise.__name__
    else:
        assert not events


@pytest.mark.asyncio
async def test_failed_request_status_codes_with_returned_status(
    sentry_init, aiohttp_client, capture_events
):
    """
    Returning a web.Response with a failed_request_status_code should not be reported to Sentry.
    """
    sentry_init(integrations=[AioHttpIntegration(failed_request_status_codes={500})])
    events = capture_events()

    async def handle(_):
        return web.Response(status=500)

    app = web.Application()
    app.router.add_get("/", handle)

    client = await aiohttp_client(app)
    resp = await client.get("/")

    assert resp.status == 500
    assert not events


@pytest.mark.asyncio
async def test_failed_request_status_codes_non_http_exception(
    sentry_init, aiohttp_client, capture_events
):
    """
    If an exception, which is not an instance of HTTPException, is raised, it should be captured, even if
    failed_request_status_codes is empty.
    """
    sentry_init(integrations=[AioHttpIntegration(failed_request_status_codes=set())])
    events = capture_events()

    async def handle(_):
        1 / 0

    app = web.Application()
    app.router.add_get("/", handle)

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 500

    (event,) = events
    assert event["exception"]["values"][0]["type"] == "ZeroDivisionError"

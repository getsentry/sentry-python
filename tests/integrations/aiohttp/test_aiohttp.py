import os
import datetime
import asyncio
import json

from contextlib import suppress
from unittest import mock

import pytest

from aiohttp import web
from aiohttp.client import ServerDisconnectedError
from aiohttp.web_request import Request
from aiohttp.web_exceptions import (
    HTTPInternalServerError,
    HTTPNetworkAuthenticationRequired,
    HTTPBadRequest,
    HTTPNotFound,
    HTTPUnavailableForLegalReasons,
)

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.aiohttp import AioHttpIntegration, create_trace_config
from sentry_sdk.consts import SPANDATA
from tests.conftest import ApproxDict


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
async def test_traces_sampler_gets_request_object_in_sampling_context(
    sentry_init,
    aiohttp_client,
    DictionaryContaining,  # noqa: N803
    ObjectDescribedBy,  # noqa: N803
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
    with start_transaction():
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
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_events
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


@pytest.mark.parametrize(
    "status_code,level",
    [
        (200, None),
        (301, None),
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
    capture_events,
    status_code,
    level,
):
    sentry_init(integrations=[AioHttpIntegration()])

    async def handler(request):
        return web.Response(status=status_code)

    raw_server = await aiohttp_raw_server(handler)

    with start_transaction():
        events = capture_events()

        client = await aiohttp_client(raw_server)
        resp = await client.get("/")
        assert resp.status == status_code
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        if level is None:
            assert "level" not in crumb
        else:
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

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with start_transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="0123456789012345678901234567890",
        ):
            client = await aiohttp_client(raw_server)
            resp = await client.get("/", headers={"bagGage": "custom=value"})

            assert (
                resp.request_info.headers["baggage"]
                == "custom=value,sentry-trace_id=0123456789012345678901234567890,sentry-sample_rand=0.500000,sentry-environment=production,sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,sentry-transaction=/interactions/other-dogs/new-dog,sentry-sample_rate=1.0,sentry-sampled=true"
            )


@pytest.mark.asyncio
async def test_request_source_disabled(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    capture_events,
):
    sentry_options = {
        "integrations": [AioHttpIntegration()],
        "traces_sample_rate": 1.0,
        "enable_http_request_source": False,
        "http_request_source_threshold_ms": 0,
    }

    sentry_init(**sentry_options)

    # server for making span request
    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def hello(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    await client.get("/")

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_http_request_source", [None, True])
async def test_request_source_enabled(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    capture_events,
    enable_http_request_source,
):
    sentry_options = {
        "integrations": [AioHttpIntegration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    # server for making span request
    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def hello(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    await client.get("/")

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data


@pytest.mark.asyncio
async def test_request_source(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_events
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    # server for making span request
    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def handler_with_outgoing_request(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", handler_with_outgoing_request)

    events = capture_events()

    client = await aiohttp_client(app)
    await client.get("/")

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert (
        data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.aiohttp.test_aiohttp"
    )
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/aiohttp/test_aiohttp.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "handler_with_outgoing_request"


@pytest.mark.asyncio
async def test_request_source_with_module_in_search_path(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_events
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    # server for making span request
    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    from aiohttp_helpers.helpers import get_request_with_client

    async def handler_with_outgoing_request(request):
        span_client = await aiohttp_client(raw_server)
        await get_request_with_client(span_client, "/")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", handler_with_outgoing_request)

    events = capture_events()

    client = await aiohttp_client(app)
    await client.get("/")

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert data.get(SPANDATA.CODE_NAMESPACE) == "aiohttp_helpers.helpers"
    assert data.get(SPANDATA.CODE_FILEPATH) == "aiohttp_helpers/helpers.py"

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "get_request_with_client"


@pytest.mark.asyncio
async def test_no_request_source_if_duration_too_short(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_events
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
    )

    # server for making span request
    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def handler_with_outgoing_request(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", handler_with_outgoing_request)

    events = capture_events()

    def fake_create_trace_context(*args, **kwargs):
        trace_context = create_trace_config()

        async def overwrite_timestamps(session, trace_config_ctx, params):
            span = trace_config_ctx.span
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)

        trace_context.on_request_end.insert(0, overwrite_timestamps)

        return trace_context

    with mock.patch(
        "sentry_sdk.integrations.aiohttp.create_trace_config",
        fake_create_trace_context,
    ):
        client = await aiohttp_client(app)
        await client.get("/")

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.asyncio
async def test_request_source_if_duration_over_threshold(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_events
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
    )

    # server for making span request
    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def handler_with_outgoing_request(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", handler_with_outgoing_request)

    events = capture_events()

    def fake_create_trace_context(*args, **kwargs):
        trace_context = create_trace_config()

        async def overwrite_timestamps(session, trace_config_ctx, params):
            span = trace_config_ctx.span
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)

        trace_context.on_request_end.insert(0, overwrite_timestamps)

        return trace_context

    with mock.patch(
        "sentry_sdk.integrations.aiohttp.create_trace_config",
        fake_create_trace_context,
    ):
        client = await aiohttp_client(app)
        await client.get("/")

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert (
        data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.aiohttp.test_aiohttp"
    )
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/aiohttp/test_aiohttp.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "handler_with_outgoing_request"


@pytest.mark.asyncio
async def test_span_origin(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    capture_events,
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
    )

    # server for making span request
    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def hello(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/")
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

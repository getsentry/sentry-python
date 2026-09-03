import asyncio
import json
import os
from contextlib import suppress
from unittest import mock

import pytest
from aiohttp import web
from aiohttp.client import ServerDisconnectedError
from aiohttp.web_exceptions import (
    HTTPBadRequest,
    HTTPInternalServerError,
    HTTPNetworkAuthenticationRequired,
    HTTPNotFound,
    HTTPUnavailableForLegalReasons,
)
from aiohttp.web_request import Request

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk._types import OVER_SIZE_LIMIT_SUBSTITUTE
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.aiohttp import (
    AioHttpIntegration,
)
from sentry_sdk.utils import SENSITIVE_DATA_SUBSTITUTE
from tests.conftest import ApproxDict
from tests.integrations.utils import (
    DATA_COLLECTION_REMOTE_ADDR_CASES,
    DATA_COLLECTION_USER_INFO_CASES,
)


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


@pytest.mark.parametrize(
    "data_collection, expect_body",
    [
        pytest.param({}, True, id="data_collection_http_bodies_default"),
        pytest.param(
            {"http_bodies": ["incoming_request"]},
            True,
            id="data_collection_http_bodies_incoming_request",
        ),
        pytest.param(
            {"http_bodies": []}, False, id="data_collection_http_bodies_empty"
        ),
    ],
)
@pytest.mark.asyncio
async def test_aiohttp_request_body_data_collection(
    sentry_init, aiohttp_client, capture_events, data_collection, expect_body
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        _experiments={"data_collection": data_collection},
    )

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
    request = event["request"]

    if expect_body:
        assert request["data"] == json.dumps(body)
    else:
        assert "data" not in request


@pytest.mark.parametrize(
    "data_collection, expect_annotated",
    [
        pytest.param(
            {"http_bodies": ["incoming_request"]},
            True,
            id="data_collection_http_bodies_incoming_request",
        ),
        pytest.param(
            {"http_bodies": []}, False, id="data_collection_http_bodies_empty"
        ),
    ],
)
@pytest.mark.asyncio
async def test_aiohttp_oversized_request_body_data_collection(
    sentry_init, aiohttp_client, capture_events, data_collection, expect_annotated
):
    """
    The gating happens before the size check. When bodies are collected, an
    oversized body is still reported as removed because of the size limit; when
    they are not, it is dropped outright with no annotation.
    """
    sentry_init(
        integrations=[AioHttpIntegration()],
        max_request_body_size="small",
        _experiments={"data_collection": data_collection},
    )

    body = "a" * 2000

    async def hello(request):
        await request.text()
        1 / 0

    app = web.Application()
    app.router.add_post("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.post("/", data=body)
    assert resp.status == 500

    (event,) = events
    request_meta = event.get("_meta", {}).get("request", {})

    if expect_annotated:
        assert event["request"]["data"] == OVER_SIZE_LIMIT_SUBSTITUTE
        assert request_meta["data"] == {"": {"rem": [["!config", "s"]]}}
    else:
        assert "data" not in event["request"]
        assert "data" not in request_meta


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


@pytest.mark.tests_internal_exceptions
@pytest.mark.asyncio
async def test_tracing_unparseable_url(sentry_init, aiohttp_client, capture_items):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    with mock.patch(
        "sentry_sdk.integrations.aiohttp.parse_url", side_effect=ValueError
    ):
        resp = await client.get("/")

    assert resp.status == 200

    (span,) = [item.payload for item in items]

    assert (
        span["name"]
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
        trace_lifecycle="stream",
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
    sentry_init, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        capture_message("It's a good day to try dividing by 0")
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("event", "span")

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 500

    msg_events = [
        i.payload for i in items if i.type == "event" and "exception" not in i.payload
    ]
    error_events = [
        i.payload for i in items if i.type == "event" and "exception" in i.payload
    ]
    spans = [i.payload for i in items if i.type == "span"]

    assert len(msg_events) == 1
    assert len(error_events) == 1
    assert len(spans) == 1

    (msg_event,) = msg_events
    (error_event,) = error_events
    (span,) = spans

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == span["trace_id"]
        == msg_event["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.asyncio
async def test_has_trace_if_performance_disabled(
    sentry_init, aiohttp_client, capture_events
):
    sentry_init(integrations=[AioHttpIntegration()], trace_lifecycle="stream")

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
    sentry_init, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        capture_message("It's a good day to try dividing by 0")
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("event", "span")

    client = await aiohttp_client(app)
    resp = await client.get("/")

    sentry_trace_header = resp.request_info.headers.get("sentry-trace")
    trace_id = sentry_trace_header.split("-")[0]

    assert resp.status == 500

    sentry_sdk.flush()

    msg_events = [
        i.payload for i in items if i.type == "event" and "exception" not in i.payload
    ]
    error_events = [
        i.payload for i in items if i.type == "event" and "exception" in i.payload
    ]
    spans = [i.payload for i in items if i.type == "span"]

    assert len(msg_events) == 1
    assert len(error_events) == 1
    assert len(spans) == 1

    (msg_event,) = msg_events
    (error_event,) = error_events
    (span,) = spans

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id

    assert span["trace_id"] == trace_id


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
@pytest.mark.parametrize(
    "pii_options,url_expected,query_expected",
    [
        ({}, False, False),
        ({"send_default_pii": True}, True, True),
        ({"send_default_pii": False}, False, False),
        (
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "denylist", "terms": []}
                    }
                }
            },
            True,
            True,
        ),
        (
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": []}
                    }
                }
            },
            True,
            False,
        ),
    ],
)
async def test_crumb_capture(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    capture_events,
    pii_options,
    url_expected,
    query_expected,
):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(
        integrations=[AioHttpIntegration()],
        before_breadcrumb=before_breadcrumb,
        trace_lifecycle="stream",
        **pii_options,
    )

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    events = capture_events()

    client = await aiohttp_client(raw_server)
    resp = await client.get("/?query=value")
    assert resp.status == 200
    capture_message("Testing!")

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    expected = {
        "http.method": "GET",
        "http.response.status_code": 200,
        "reason": "OK",
    }

    if url_expected:
        if query_expected:
            expected["url"] = f"http://127.0.0.1:{raw_server.port}/?query=value"
            expected["http.query"] = "query=value"
        else:
            expected["url"] = (
                f"http://127.0.0.1:{raw_server.port}/?query=%5BFiltered%5D"
            )
            expected["http.query"] = "query=%5BFiltered%5D"

    assert crumb["data"] == ApproxDict(expected)


@pytest.mark.parametrize(
    "status_code,level,reason",
    [
        (200, None, "OK"),
        (301, None, "Moved Permanently"),
        (403, "warning", "Forbidden"),
        (405, "warning", "Method Not Allowed"),
        (500, "error", "Internal Server Error"),
    ],
)
@pytest.mark.parametrize(
    "pii_options,url_expected,query_expected",
    [
        ({}, False, False),
        ({"send_default_pii": True}, True, True),
        ({"send_default_pii": False}, False, False),
        (
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "denylist", "terms": []}
                    }
                }
            },
            True,
            True,
        ),
        (
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": []}
                    }
                }
            },
            True,
            False,
        ),
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
    reason,
    pii_options,
    url_expected,
    query_expected,
):
    sentry_init(
        integrations=[AioHttpIntegration()], trace_lifecycle="stream", **pii_options
    )

    async def handler(request):
        return web.Response(status=status_code)

    raw_server = await aiohttp_raw_server(handler)

    events = capture_events()

    client = await aiohttp_client(raw_server)
    resp = await client.get("/?query=value")
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

    expected = {
        "http.method": "GET",
        "http.response.status_code": status_code,
        "reason": reason,
    }

    if url_expected:
        if query_expected:
            expected["url"] = f"http://127.0.0.1:{raw_server.port}/?query=value"
            expected["http.query"] = "query=value"
        else:
            expected["url"] = (
                f"http://127.0.0.1:{raw_server.port}/?query=%5BFiltered%5D"
            )
            expected["http.query"] = "query=%5BFiltered%5D"

    assert crumb["data"] == ApproxDict(expected)


@pytest.mark.asyncio
async def test_outgoing_trace_headers_append_to_baggage(
    sentry_init, aiohttp_raw_server, aiohttp_client
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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
    capture_items,
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
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

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    (span, segment) = [item.payload for item in items]

    assert span["name"].startswith("GET")

    assert SPANDATA.CODE_LINENO not in span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in span["attributes"]
    assert SPANDATA.CODE_FILEPATH not in span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_http_request_source", [None, True])
async def test_request_source_enabled(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    capture_items,
    enable_http_request_source,
):
    extra_options = {}
    if enable_http_request_source is not None:
        extra_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        http_request_source_threshold_ms=0,
        **extra_options,
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

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    (span, segment) = [item.payload for item in items]

    assert span["name"].startswith("GET")

    assert "code.line.number" in span["attributes"]
    assert "code.namespace" in span["attributes"]
    assert "code.file.path" in span["attributes"]
    assert "code.function" in span["attributes"]


@pytest.mark.asyncio
async def test_request_source(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    (span, segment) = [item.payload for item in items]

    assert span["name"].startswith("GET")

    assert "code.line.number" in span["attributes"]
    assert "code.namespace" in span["attributes"]
    assert "code.file.path" in span["attributes"]
    assert "code.function" in span["attributes"]

    assert type(span["attributes"]["code.line.number"]) == int
    assert span["attributes"]["code.line.number"] > 0
    assert (
        span["attributes"]["code.namespace"]
        == "tests.integrations.aiohttp.test_aiohttp"
    )
    assert span["attributes"]["code.file.path"].endswith(
        "tests/integrations/aiohttp/test_aiohttp.py"
    )

    is_relative_path = span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert span["attributes"]["code.function"] == "handler_with_outgoing_request"


@pytest.mark.asyncio
async def test_request_source_with_module_in_search_path(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_items
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    (span, segment) = [item.payload for item in items]

    assert span["name"].startswith("GET")

    assert "code.line.number" in span["attributes"]
    assert "code.namespace" in span["attributes"]
    assert "code.file.path" in span["attributes"]
    assert "code.function" in span["attributes"]

    assert type(span["attributes"]["code.line.number"]) == int
    assert span["attributes"]["code.line.number"] > 0
    assert span["attributes"]["code.namespace"] == "aiohttp_helpers.helpers"
    assert span["attributes"]["code.file.path"] == "aiohttp_helpers/helpers.py"

    is_relative_path = span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert span["attributes"]["code.function"] == "get_request_with_client"


@pytest.mark.asyncio
async def test_no_request_source_if_duration_too_short(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        enable_http_request_source=True,
        http_request_source_threshold_ms=10**10,
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

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    (
        span,
        segment,
    ) = [item.payload for item in items]

    assert span["name"].startswith("GET")

    assert SPANDATA.CODE_LINENO not in span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in span["attributes"]
    assert SPANDATA.CODE_FILEPATH not in span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in span["attributes"]


@pytest.mark.asyncio
async def test_request_source_if_duration_over_threshold(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
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

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    (span, segment) = [item.payload for item in items]

    assert span["name"].startswith("GET")

    assert "code.line.number" in span["attributes"]
    assert "code.namespace" in span["attributes"]
    assert "code.file.path" in span["attributes"]
    assert "code.function" in span["attributes"]

    assert type(span["attributes"]["code.line.number"]) == int
    assert span["attributes"]["code.line.number"] > 0
    assert (
        span["attributes"]["code.namespace"]
        == "tests.integrations.aiohttp.test_aiohttp"
    )
    assert span["attributes"]["code.file.path"].endswith(
        "tests/integrations/aiohttp/test_aiohttp.py"
    )

    is_relative_path = span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert span["attributes"]["code.function"] == "handler_with_outgoing_request"


@pytest.mark.asyncio
async def test_span_origin(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    capture_items,
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        trace_lifecycle="stream",
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

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    (span, segment) = [item.payload for item in items]
    assert span["attributes"]["sentry.origin"] == "auto.http.aiohttp"
    assert segment["attributes"]["sentry.origin"] == "auto.http.aiohttp"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("send_pii", [True, False])
async def test_tracing(sentry_init, aiohttp_client, capture_items, send_pii):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_pii,
        trace_lifecycle="stream",
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 200

    sentry_sdk.flush()

    # The server-side http.server span is the only segment. The aiohttp_client
    # fixture's outgoing http.client span is suppressed because there is no
    # active span when the test client makes the request.
    assert len(items) == 1

    (server_span,) = [item.payload for item in items]

    assert server_span["is_segment"] is True
    assert (
        server_span["name"]
        == "tests.integrations.aiohttp.test_aiohttp.test_tracing.<locals>.hello"
    )
    assert server_span["attributes"]["sentry.op"] == "http.server"
    assert server_span["attributes"]["sentry.origin"] == "auto.http.aiohttp"
    assert server_span["attributes"]["http.response.status_code"] == 200
    assert server_span["attributes"]["sentry.segment.name.source"] == "component"
    assert server_span["status"] == "ok"
    # No query string on the request, so the attribute should be omitted.
    assert "url.query" not in server_span["attributes"]

    # Request attributes derived directly from the aiohttp request.
    assert server_span["attributes"]["http.request.method"] == "GET"

    if send_pii:
        assert "client.address" in server_span["attributes"]
        assert "user.ip_address" in server_span["attributes"]

        url_full = server_span["attributes"]["url.full"]
        assert url_full.startswith("http://127.0.0.1:")
        assert url_full.endswith("/")

        url_path = server_span["attributes"]["url.path"]
        assert url_path == "/"
    else:
        assert "url.full" not in server_span["attributes"]
        assert "url.path" not in server_span["attributes"]
        assert "url.query" not in server_span["attributes"]

        assert "client.address" not in server_span["attributes"]
        assert "user.ip_address" not in server_span["attributes"]

    # aiohttp's test client always sends a Host header; we assert it propagates
    # into the span attributes via _filter_headers.
    assert "http.request.header.host" in server_span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("init_kwargs, expect_ip", DATA_COLLECTION_USER_INFO_CASES)
async def test_user_address_with_data_collection(
    sentry_init, aiohttp_client, capture_items, init_kwargs, expect_ip
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 200

    sentry_sdk.flush()

    (server_span,) = [item.payload for item in items]
    assert server_span["attributes"]["sentry.origin"] == "auto.http.aiohttp"

    if expect_ip:
        assert server_span["attributes"]["client.address"] == "127.0.0.1"
        assert server_span["attributes"]["user.ip_address"] == "127.0.0.1"
    else:
        assert "client.address" not in server_span["attributes"]
        assert "user.ip_address" not in server_span["attributes"]


@pytest.mark.asyncio
async def test_sensitive_header_scrubbing(sentry_init, aiohttp_client, capture_items):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    resp = await client.get(
        "/",
        headers={
            "Authorization": "Bearer secret-token",
            "X-Custom-Header": "passthrough",
        },
    )
    assert resp.status == 200

    sentry_sdk.flush()

    (server_span,) = [item.payload for item in items]

    # send_default_pii defaults to False, so _filter_headers substitutes
    # sensitive headers with SENSITIVE_DATA_SUBSTITUTE ("[Filtered]"). The
    # original token must not leak.
    assert (
        server_span["attributes"]["http.request.header.authorization"]
        == SENSITIVE_DATA_SUBSTITUTE
    )
    # Non-sensitive headers pass through untouched.
    assert (
        server_span["attributes"]["http.request.header.x-custom-header"]
        == "passthrough"
    )


@pytest.mark.parametrize(
    "options,expected",
    [
        pytest.param(
            {
                "send_default_pii": True,
                "data_collection": None,
            },
            {
                "authorization": "[Filtered]",
                "custom": "foobar",
                "cookie": "[Filtered]",
            },
            id="enabled_send_default_pii_redacts_auth_header_due_to_data_collection_default_settings",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "data_collection": None,
            },
            {
                "authorization": "[Filtered]",
                "custom": "foobar",
                "cookie": "[Filtered]",
            },
            id="disabled_send_default_pii_redacts_auth_header_due_to_data_collection_default_settings",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "data_collection": {"http_headers": {"request": {"mode": "off"}}},
            },
            None,
            id="data_collection_off_does_not_add_headers",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "data_collection": {"http_headers": {"request": {"mode": "allowlist"}}},
            },
            {
                "authorization": "[Filtered]",
                "custom": "[Filtered]",
                "cookie": "[Filtered]",
            },
            id="data_collection_allow_list_redacts_terms_that_do_not_appear",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "data_collection": {
                    "http_headers": {
                        "request": {"mode": "allowlist", "terms": ["Authorization"]}
                    }
                },
            },
            {
                "authorization": "[Filtered]",
                "custom": "[Filtered]",
                "cookie": "[Filtered]",
            },
            id="data_collection_allow_list_redacts_sensitive_terms_even_when_provided_by_user",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "data_collection": {
                    "http_headers": {
                        "request": {"mode": "allowlist", "terms": ["custom"]}
                    }
                },
            },
            {
                "authorization": "[Filtered]",
                "custom": "foobar",
                "cookie": "[Filtered]",
            },
            id="data_collection_allow_list_does_not_redact_provided_term",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "data_collection": {
                    "http_headers": {
                        "request": {"mode": "denylist", "terms": ["custom"]}
                    }
                },
            },
            {
                "authorization": "[Filtered]",
                "custom": "[Filtered]",
                "cookie": "[Filtered]",
            },
            id="data_collection_deny_list_redacts_sensitive_terms_when_provided_by_user",
        ),
        pytest.param(
            {
                "send_default_pii": False,
                "data_collection": {
                    "http_headers": {
                        "request": {"mode": "allowlist", "terms": ["cookie"]}
                    }
                },
            },
            {
                "authorization": "[Filtered]",
                "custom": "[Filtered]",
                "cookie": "[Filtered]",
            },
            id="data_collection_cookie_is_always_redacted_even_when_allow_listed",
        ),
    ],
)
@pytest.mark.asyncio
async def test_sensitive_header_passthrough_with_pii(
    sentry_init, aiohttp_client, capture_items, options, expected, request
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=options["send_default_pii"],
        trace_lifecycle="stream",
        _experiments={
            "data_collection": options["data_collection"],
        },
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get(
        "/",
        headers={
            "Authorization": "Bearer secret-token",
            "x-custom-header": "foobar",
            "Cookie": "sessionid=secret",
        },
    )

    sentry_sdk.flush()

    (server_span,) = [item.payload for item in items]

    if expected is None:
        assert "http.request.header.authorization" not in server_span["attributes"]
        assert "http.request.header.cookie" not in server_span["attributes"]
    else:
        assert (
            server_span["attributes"]["http.request.header.authorization"]
            == expected["authorization"]
        )
        assert (
            server_span["attributes"]["http.request.header.x-custom-header"]
            == expected["custom"]
        )
        assert (
            server_span["attributes"]["http.request.header.cookie"]
            == expected["cookie"]
        )


@pytest.mark.asyncio
async def test_sensitive_header_passthrough_with_pii_without_data_collection(
    sentry_init, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/", headers={"Authorization": "Bearer secret-token"})

    sentry_sdk.flush()

    (server_span,) = [item.payload for item in items]

    # With send_default_pii=True, _filter_headers is a no-op and the original
    # value reaches the span attribute.
    assert (
        server_span["attributes"]["http.request.header.authorization"]
        == "Bearer secret-token"
    )
    # client.address and user.ip_address is captured under send_default_pii=True.
    assert server_span["attributes"]["client.address"] == "127.0.0.1"
    assert server_span["attributes"]["user.ip_address"] == "127.0.0.1"


@pytest.mark.asyncio
@pytest.mark.parametrize("send_pii", [True, False])
async def test_url_query_attribute(
    sentry_init, aiohttp_client, capture_items, send_pii
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_pii,
        trace_lifecycle="stream",
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    resp = await client.get("/?foo=bar&baz=qux")
    assert resp.status == 200

    sentry_sdk.flush()

    assert len(items) == 1
    (server_segment,) = [item.payload for item in items]

    if send_pii:
        assert server_segment["attributes"]["url.query"] == "foo=bar&baz=qux"
    else:
        assert "url.query" not in server_segment["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,transaction_style,expected_name,expected_source",
    [
        (
            "/message",
            "handler_name",
            "tests.integrations.aiohttp.test_aiohttp."
            "test_transaction_style.<locals>.hello",
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
    capture_items,
    url,
    transaction_style,
    expected_name,
    expected_source,
):
    sentry_init(
        integrations=[AioHttpIntegration(transaction_style=transaction_style)],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/{var}", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    resp = await client.get(url)
    assert resp.status == 200

    sentry_sdk.flush()

    assert len(items) == 1
    (server_segment,) = [item.payload for item in items]

    assert server_segment["name"] == expected_name
    assert server_segment["is_segment"]
    assert server_segment["attributes"]["sentry.segment.name.source"] == expected_source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,expected_route",
    [
        ("/message", "/{var}"),
    ],
)
async def test_http_route(
    sentry_init,
    aiohttp_client,
    capture_items,
    url,
    expected_route,
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/{var}", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get(url)

    sentry_sdk.flush()
    (segment,) = (item.payload for item in items if item.payload.get("is_segment"))
    assert segment["attributes"][SPANDATA.HTTP_ROUTE] == expected_route


@pytest.mark.asyncio
async def test_server_error(sentry_init, aiohttp_client, capture_items):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("event", "span")

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 500

    sentry_sdk.flush()

    # 1 error event + 1 span (server http.server)
    assert len(items) == 2

    error_event = items[0]
    assert error_event.type == "event"
    assert error_event.payload["exception"]["values"][0]["type"] == "ZeroDivisionError"

    server_span = items[1].payload

    # The integration's generic Exception path reraises without recording
    # http.response.status_code on the server span. StreamedSpan.__exit__
    # observes the propagating exception and sets status to "error".
    assert server_span["attributes"]["sentry.op"] == "http.server"
    assert "http.response.status_code" not in server_span["attributes"]
    assert server_span["status"] == "error"


@pytest.mark.asyncio
async def test_http_exception(sentry_init, aiohttp_client, capture_items):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        raise web.HTTPForbidden()

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 403

    sentry_sdk.flush()

    assert len(items) == 1
    (server_span,) = [item.payload for item in items]

    assert server_span["attributes"]["sentry.op"] == "http.server"
    assert server_span["attributes"]["http.response.status_code"] == 403
    assert server_span["status"] == "error"


@pytest.mark.asyncio
async def test_http_exception_ok_status_not_overridden(
    sentry_init, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def hello(request):
        raise web.HTTPFound("https://example.com")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app, server_kwargs={"skip_url_asserts": True})
    resp = await client.get("/", allow_redirects=False)
    assert resp.status == 302

    sentry_sdk.flush()

    assert len(items) == 1
    (server_span,) = [item.payload for item in items]

    assert server_span["attributes"]["sentry.op"] == "http.server"
    assert server_span["attributes"]["http.response.status_code"] == 302
    assert server_span["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("send_pii", [True, False])
async def test_outgoing_client_span(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_items, send_pii
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_pii,
        trace_lifecycle="stream",
    )

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def hello(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/?foo=bar")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    # 2 spans, finished inner-first:
    #   #0 inner http.client (server -> raw_server)
    #   #1 server http.server
    assert len(items) == 2

    inner_client_span = items[0].payload
    server_span = items[1].payload

    assert server_span["attributes"]["sentry.op"] == "http.server"

    assert inner_client_span["is_segment"] is False
    assert inner_client_span["name"].startswith("GET ")
    assert inner_client_span["attributes"]["sentry.op"] == "http.client"
    assert inner_client_span["attributes"]["sentry.origin"] == "auto.http.aiohttp"
    assert inner_client_span["attributes"]["http.request.method"] == "GET"
    assert inner_client_span["attributes"]["http.response.status_code"] == 200
    assert inner_client_span["status"] == "ok"

    if send_pii:
        assert inner_client_span["attributes"]["url.query"] == "foo=bar"

        url_full = inner_client_span["attributes"]["url.full"]

        assert url_full.startswith("http://127.0.0.1:")
        assert "?foo=bar" in url_full

        assert inner_client_span["attributes"]["url.path"] == "/"


@pytest.mark.asyncio
async def test_outgoing_trace_headers(
    sentry_init, aiohttp_raw_server, aiohttp_client, capture_items
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    items = capture_items("span")

    client = await aiohttp_client(raw_server)
    resp = await client.get("/")

    sentry_sdk.flush()

    # The outgoing http.client span is suppressed because there is no active
    # span when the test client makes the request.
    assert len(items) == 0

    # Even though no span is created, the trace propagation headers must still
    # be added to the outgoing request so the trace is not broken.
    assert "sentry-trace" in resp.request_info.headers
    assert "baggage" in resp.request_info.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_user_ip_address_on_all_spans(
    sentry_init, aiohttp_client, capture_items, send_default_pii
):
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    async def hello(request):
        with sentry_sdk.traces.start_span(name="child-span"):
            pass
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    child_span, server_span = [item.payload for item in items]

    assert server_span["attributes"]["sentry.segment.name.source"] == "component"
    assert "sentry.segment.name.source" not in child_span["attributes"]

    if send_default_pii:
        assert server_span["attributes"]["user.ip_address"] == "127.0.0.1"
        assert child_span["attributes"]["user.ip_address"] == "127.0.0.1"
    else:
        assert "user.ip_address" not in server_span["attributes"]
        assert "user.ip_address" not in child_span["attributes"]


_QUERY_PARAM_DATA_COLLECTION_CASES = [
    pytest.param(
        {"send_default_pii": True},
        "toy=tennisball&color=red&auth=secret",
        id="send_default_pii_true",
    ),
    pytest.param(
        {"send_default_pii": False},
        None,
        id="send_default_pii_false",
    ),
    pytest.param(
        {},
        None,
        id="defaults",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {}}},
        "toy=tennisball&color=red&auth=%5BFiltered%5D",
        id="data_collection_denylist_default",
    ),
    pytest.param(
        {
            "_experiments": {
                "data_collection": {
                    "url_query_params": {"mode": "denylist", "terms": ["toy"]}
                }
            }
        },
        "toy=%5BFiltered%5D&color=red&auth=%5BFiltered%5D",
        id="data_collection_denylist_custom_terms",
    ),
    pytest.param(
        {
            "_experiments": {
                "data_collection": {
                    "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                }
            }
        },
        "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
        id="data_collection_allowlist",
    ),
    pytest.param(
        {
            "_experiments": {
                "data_collection": {
                    "url_query_params": {"mode": "allowlist", "terms": ["auth"]}
                }
            }
        },
        "toy=%5BFiltered%5D&color=%5BFiltered%5D&auth=%5BFiltered%5D",
        id="data_collection_allowlist_sensitive_term",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"url_query_params": {"mode": "off"}}}},
        None,
        id="data_collection_off",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "_experiments": {"data_collection": {"url_query_params": {"mode": "off"}}},
        },
        None,
        id="data_collection_wins_over_send_default_pii",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_query", _QUERY_PARAM_DATA_COLLECTION_CASES
)
async def test_server_url_query_data_collection(
    sentry_init, aiohttp_client, capture_items, init_kwargs, expected_query
):
    init_kwargs = dict(init_kwargs)
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    async def hello(request):
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    resp = await client.get("/?toy=tennisball&color=red&auth=secret")
    assert resp.status == 200

    sentry_sdk.flush()

    (server_span,) = [item.payload for item in items]

    if expected_query is None:
        assert "url.query" not in server_span["attributes"]
    else:
        assert server_span["attributes"]["url.query"] == expected_query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_query", _QUERY_PARAM_DATA_COLLECTION_CASES
)
async def test_client_url_query_data_collection(
    sentry_init,
    aiohttp_raw_server,
    aiohttp_client,
    capture_items,
    init_kwargs,
    expected_query,
):
    init_kwargs = dict(init_kwargs)
    sentry_init(
        integrations=[AioHttpIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    async def handler(request):
        return web.Response(text="OK")

    raw_server = await aiohttp_raw_server(handler)

    async def hello(request):
        span_client = await aiohttp_client(raw_server)
        await span_client.get("/?toy=tennisball&color=red&auth=secret")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get(r"/", hello)

    items = capture_items("span")

    client = await aiohttp_client(app)
    await client.get("/")

    sentry_sdk.flush()

    inner_client_span = items[0].payload

    if expected_query is None:
        assert "url.query" not in inner_client_span["attributes"]
    else:
        assert inner_client_span["attributes"]["url.query"] == expected_query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_query", _QUERY_PARAM_DATA_COLLECTION_CASES
)
async def test_server_url_query_data_collection_event_processor(
    sentry_init, aiohttp_client, capture_events, init_kwargs, expected_query
):
    init_kwargs = dict(init_kwargs)
    sentry_init(integrations=[AioHttpIntegration()], **init_kwargs)

    async def hello(request):
        1 / 0

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/?toy=tennisball&color=red&auth=secret")
    assert resp.status == 500

    (event,) = events

    host = event["request"]["headers"]["Host"]
    assert event["request"]["url"] == "http://{host}/".format(host=host)
    assert event["request"]["method"] == "GET"

    if "data_collection" not in init_kwargs.get("_experiments", {}):
        assert (
            event["request"]["query_string"] == "toy=tennisball&color=red&auth=secret"
        )
    elif expected_query is None:
        assert "query_string" not in event["request"]
    else:
        assert event["request"]["query_string"] == expected_query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expect_remote_addr", DATA_COLLECTION_REMOTE_ADDR_CASES
)
async def test_remote_addr_data_collection(
    sentry_init, aiohttp_client, capture_events, init_kwargs, expect_remote_addr
):
    sentry_init(integrations=[AioHttpIntegration()], **init_kwargs)

    async def hello(request):
        capture_message("hi")
        return web.Response(text="hello")

    app = web.Application()
    app.router.add_get("/", hello)

    events = capture_events()

    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 200

    (event,) = events
    if expect_remote_addr:
        assert event["request"]["env"] == {"REMOTE_ADDR": "127.0.0.1"}
    else:
        assert "env" not in event["request"]

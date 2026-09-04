import os
from unittest import mock

import httpx2
import pytest

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import MATCH_ALL, OP, SPANDATA
from sentry_sdk.integrations.httpx2 import Httpx2Integration
from tests.conftest import ApproxDict


def _get_http_client_span(items):
    return next(
        item.payload
        for item in items
        if item.payload.get("attributes", {}).get("sentry.op") == OP.HTTP_CLIENT
    )


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_crumb_capture_and_hint_sync(
    sentry_init, capture_events, httpx2_mock, send_default_pii
):
    httpx2_mock.add_response()

    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(
        integrations=[Httpx2Integration()],
        before_breadcrumb=before_breadcrumb,
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = httpx2.Client().get(url)

        assert response.status_code == 200
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        assert crumb["category"] == "httplib"

        if send_default_pii:
            assert crumb["data"] == ApproxDict(
                {
                    "url": url,
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_FRAGMENT: "",
                    SPANDATA.HTTP_QUERY: "",
                    SPANDATA.HTTP_STATUS_CODE: 200,
                    "reason": "OK",
                    "extra": "foo",
                }
            )
        else:
            assert crumb["data"] == ApproxDict(
                {
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_STATUS_CODE: 200,
                    "reason": "OK",
                    "extra": "foo",
                }
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_crumb_capture_and_hint_async(
    sentry_init, capture_events, httpx2_mock, send_default_pii
):
    httpx2_mock.add_response()

    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(
        integrations=[Httpx2Integration()],
        before_breadcrumb=before_breadcrumb,
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = await httpx2.AsyncClient().get(url)

        assert response.status_code == 200
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        assert crumb["category"] == "httplib"
        if send_default_pii:
            assert crumb["data"] == ApproxDict(
                {
                    "url": url,
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_FRAGMENT: "",
                    SPANDATA.HTTP_QUERY: "",
                    SPANDATA.HTTP_STATUS_CODE: 200,
                    "reason": "OK",
                    "extra": "foo",
                }
            )
        else:
            assert crumb["data"] == ApproxDict(
                {
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_STATUS_CODE: 200,
                    "reason": "OK",
                    "extra": "foo",
                }
            )


def test_crumb_capture_without_span_sync(sentry_init, capture_events, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    events = capture_events()

    response = httpx2.Client().get(url)

    assert response.status_code == 200
    capture_message("Testing!")

    sentry_sdk.flush()

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
        }
    )


@pytest.mark.asyncio
async def test_crumb_capture_without_span_async(
    sentry_init, capture_events, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    events = capture_events()

    response = await httpx2.AsyncClient().get(url)

    assert response.status_code == 200
    capture_message("Testing!")

    sentry_sdk.flush()

    (event,) = events

    crumb = event["breadcrumbs"]["values"][0]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
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
@pytest.mark.parametrize("send_default_pii", [True, False])
def test_crumb_capture_client_error_sync(
    sentry_init, capture_events, httpx2_mock, status_code, level, send_default_pii
):
    httpx2_mock.add_response(status_code=status_code)

    sentry_init(
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = httpx2.Client().get(url)

        assert response.status_code == status_code
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        assert crumb["category"] == "httplib"

        if level is None:
            assert "level" not in crumb
        else:
            assert crumb["level"] == level

        if send_default_pii:
            assert crumb["data"] == ApproxDict(
                {
                    "url": url,
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_FRAGMENT: "",
                    SPANDATA.HTTP_QUERY: "",
                    SPANDATA.HTTP_STATUS_CODE: status_code,
                }
            )
        else:
            assert crumb["data"] == ApproxDict(
                {
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_STATUS_CODE: status_code,
                }
            )


@pytest.mark.asyncio
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
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_crumb_capture_client_error_async(
    sentry_init, capture_events, httpx2_mock, status_code, level, send_default_pii
):
    httpx2_mock.add_response(status_code=status_code)

    sentry_init(
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = await httpx2.AsyncClient().get(url)

        assert response.status_code == status_code
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        assert crumb["category"] == "httplib"

        if level is None:
            assert "level" not in crumb
        else:
            assert crumb["level"] == level

        if send_default_pii:
            assert crumb["data"] == ApproxDict(
                {
                    "url": url,
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_FRAGMENT: "",
                    SPANDATA.HTTP_QUERY: "",
                    SPANDATA.HTTP_STATUS_CODE: status_code,
                }
            )
        else:
            assert crumb["data"] == ApproxDict(
                {
                    SPANDATA.HTTP_METHOD: "GET",
                    SPANDATA.HTTP_STATUS_CODE: status_code,
                }
            )


@pytest.mark.parametrize(
    "trace_propagation_targets,url,trace_propagated",
    [
        [
            None,
            "https://example.com/",
            False,
        ],
        [
            [],
            "https://example.com/",
            False,
        ],
        [
            [MATCH_ALL],
            "https://example.com/",
            True,
        ],
        [
            ["https://example.com/"],
            "https://example.com/",
            True,
        ],
        [
            ["https://example.com/"],
            "https://example.com",
            False,
        ],
        [
            ["https://example.com"],
            "https://example.com",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://example.net",
            False,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net/some/thing",
            True,
        ],
    ],
)
def test_option_trace_propagation_targets_sync(
    sentry_init,
    httpx2_mock,
    trace_propagation_targets,
    url,
    trace_propagated,
):
    httpx2_mock.add_response()

    sentry_init(
        release="test",
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        integrations=[Httpx2Integration()],
    )

    with sentry_sdk.traces.start_span(name="span"):
        httpx2.Client().get(url)

    request_headers = httpx2_mock.get_request().headers

    if trace_propagated:
        assert "sentry-trace" in request_headers
    else:
        assert "sentry-trace" not in request_headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trace_propagation_targets,url,trace_propagated",
    [
        [
            None,
            "https://example.com/",
            False,
        ],
        [
            [],
            "https://example.com/",
            False,
        ],
        [
            [MATCH_ALL],
            "https://example.com/",
            True,
        ],
        [
            ["https://example.com/"],
            "https://example.com/",
            True,
        ],
        [
            ["https://example.com/"],
            "https://example.com",
            False,
        ],
        [
            ["https://example.com"],
            "https://example.com",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://example.net",
            False,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net/some/thing",
            True,
        ],
    ],
)
async def test_option_trace_propagation_targets_async(
    sentry_init,
    httpx2_mock,
    trace_propagation_targets,
    url,
    trace_propagated,
):
    httpx2_mock.add_response()

    sentry_init(
        release="test",
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        integrations=[Httpx2Integration()],
    )

    with sentry_sdk.traces.start_span(name="span"):
        await httpx2.AsyncClient().get(url)

    request_headers = httpx2_mock.get_request().headers

    if trace_propagated:
        assert "sentry-trace" in request_headers
    else:
        assert "sentry-trace" not in request_headers


def test_outgoing_trace_headers_sync(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        response = httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert response.request.headers[
        "sentry-trace"
    ] == "{trace_id}-{span_id}-{sampled}".format(
        trace_id=http_span["trace_id"],
        span_id=http_span["span_id"],
        sampled=1,
    )


@pytest.mark.asyncio
async def test_outgoing_trace_headers_async(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        response = await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert response.request.headers[
        "sentry-trace"
    ] == "{trace_id}-{span_id}-{sampled}".format(
        trace_id=http_span["trace_id"],
        span_id=http_span["span_id"],
        sampled=1,
    )


def test_outgoing_trace_headers_append_to_baggage_sync(
    sentry_init,
    capture_items,
    httpx2_mock,
):
    httpx2_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Httpx2Integration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with sentry_sdk.traces.start_span(name="test"):
            response = httpx2.Client().get(url, headers={"baGGage": "custom=data"})

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    baggage = response.request.headers["baggage"]
    assert baggage.startswith("custom=data,")
    assert f"sentry-trace_id={http_span['trace_id']}" in baggage
    assert "sentry-sample_rand=0.500000" in baggage
    assert "sentry-sampled=true" in baggage


@pytest.mark.asyncio
async def test_outgoing_trace_headers_append_to_baggage_async(
    sentry_init,
    capture_items,
    httpx2_mock,
):
    httpx2_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Httpx2Integration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with sentry_sdk.traces.start_span(name="test"):
            response = await httpx2.AsyncClient().get(
                url, headers={"baGGage": "custom=data"}
            )

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    baggage = response.request.headers["baggage"]
    assert baggage.startswith("custom=data,")
    assert f"sentry-trace_id={http_span['trace_id']}" in baggage
    assert "sentry-sample_rand=0.500000" in baggage
    assert "sentry-sampled=true" in baggage


def test_outgoing_trace_headers_no_current_span(sentry_init, httpx2_mock):
    """
    Even when there is no active span, trace propagation headers should still
    be attached to outgoing requests when span streaming is enabled.

    This is deliberately different from the legacy (transaction-based) approach,
    which does not propagate outside of a transaction (see
    ``test_do_not_propagate_outside_transaction``). The streamed approach
    propagates from the current scope's propagation context regardless of
    whether a span is active.
    """
    httpx2_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        trace_propagation_targets=[MATCH_ALL],
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    httpx2_client = httpx2.Client()

    # No start_span / start_transaction -> get_current_span() is None
    assert sentry_sdk.traces.get_current_span() is None

    response = httpx2_client.get(url)

    assert response.status_code == 200

    # Trace is still propagated from the scope's propagation context
    request_headers = httpx2_mock.get_request().headers
    assert "sentry-trace" in request_headers
    assert "baggage" in request_headers

    # The propagated headers describe a single, coherent trace: the trace_id in
    # sentry-trace matches the one carried in baggage.
    trace_id = request_headers["sentry-trace"].split("-")[0]
    assert f"sentry-trace_id={trace_id}" in request_headers["baggage"]


@pytest.mark.asyncio
async def test_outgoing_trace_headers_no_current_span_async(sentry_init, httpx2_mock):
    """
    The async client must match the sync client: trace propagation headers are
    attached to outgoing requests even when there is no active span and span
    streaming is enabled.
    """
    httpx2_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        trace_propagation_targets=[MATCH_ALL],
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    httpx2_client = httpx2.AsyncClient()

    # No start_span / start_transaction -> get_current_span() is None
    assert sentry_sdk.traces.get_current_span() is None

    response = await httpx2_client.get(url)

    assert response.status_code == 200

    # Trace is still propagated from the scope's propagation context
    request_headers = httpx2_mock.get_request().headers
    assert "sentry-trace" in request_headers
    assert "baggage" in request_headers

    # The propagated headers describe a single, coherent trace: the trace_id in
    # sentry-trace matches the one carried in baggage.
    trace_id = request_headers["sentry-trace"].split("-")[0]
    assert f"sentry-trace_id={trace_id}" in request_headers["baggage"]


def test_request_source_disabled_sync(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


@pytest.mark.asyncio
async def test_request_source_disabled_async(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


@pytest.mark.parametrize("enable_http_request_source", [None, True])
def test_request_source_enabled_sync(
    sentry_init,
    capture_items,
    enable_http_request_source,
    httpx2_mock,
):
    httpx2_mock.add_response()

    sentry_options = {
        "integrations": [Httpx2Integration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
        "trace_lifecycle": "stream",
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_http_request_source", [None, True])
async def test_request_source_enabled_async(
    sentry_init,
    capture_items,
    enable_http_request_source,
    httpx2_mock,
):
    httpx2_mock.add_response()

    sentry_options = {
        "integrations": [Httpx2Integration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
        "trace_lifecycle": "stream",
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]


def test_request_source_sync(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert (
        http_span["attributes"][SPANDATA.CODE_NAMESPACE]
        == "tests.integrations.httpx2.test_httpx2"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx2/test_httpx2.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert http_span["attributes"][SPANDATA.CODE_FUNCTION] == "test_request_source_sync"


@pytest.mark.asyncio
async def test_request_source_async(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert (
        http_span["attributes"][SPANDATA.CODE_NAMESPACE]
        == "tests.integrations.httpx2.test_httpx2"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx2/test_httpx2.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION] == "test_request_source_async"
    )


def test_request_source_with_module_in_search_path_sync(
    sentry_init, capture_items, httpx2_mock
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        from httpx2_helpers.helpers import get_request_with_client

        get_request_with_client(httpx2.Client(), url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert http_span["attributes"][SPANDATA.CODE_NAMESPACE] == "httpx2_helpers.helpers"
    assert http_span["attributes"]["code.file.path"] == "httpx2_helpers/helpers.py"

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert http_span["attributes"][SPANDATA.CODE_FUNCTION] == "get_request_with_client"


@pytest.mark.asyncio
async def test_request_source_with_module_in_search_path_async(
    sentry_init, capture_items, httpx2_mock
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        from httpx2_helpers.helpers import async_get_request_with_client

        await async_get_request_with_client(httpx2.AsyncClient(), url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert http_span["attributes"][SPANDATA.CODE_NAMESPACE] == "httpx2_helpers.helpers"
    assert http_span["attributes"]["code.file.path"] == "httpx2_helpers/helpers.py"

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "async_get_request_with_client"
    )


def test_no_request_source_if_duration_too_short_sync(
    sentry_init, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold so high no real request will ever exceed it
        http_request_source_threshold_ms=9999999,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


@pytest.mark.asyncio
async def test_no_request_source_if_duration_too_short_async(
    sentry_init, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold so high no real request will ever exceed it
        http_request_source_threshold_ms=9999999,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


def test_request_source_if_duration_over_threshold_sync(
    sentry_init, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold of 0 means any non-zero duration qualifies
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert (
        http_span["attributes"][SPANDATA.CODE_NAMESPACE]
        == "tests.integrations.httpx2.test_httpx2"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx2/test_httpx2.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "test_request_source_if_duration_over_threshold_sync"
    )


@pytest.mark.asyncio
async def test_request_source_if_duration_over_threshold_async(
    sentry_init, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold of 0 means any non-zero duration qualifies
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert (
        http_span["attributes"][SPANDATA.CODE_NAMESPACE]
        == "tests.integrations.httpx2.test_httpx2"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx2/test_httpx2.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "test_request_source_if_duration_over_threshold_async"
    )


def test_span_origin_sync(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["sentry.origin"] == "auto.http.httpx2"


@pytest.mark.asyncio
async def test_span_origin_async(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["sentry.origin"] == "auto.http.httpx2"


def test_http_url_attributes_sync(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/?foo=bar#frag"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["url.full"] == "http://example.com/?foo=bar#frag"
    assert http_span["attributes"]["url.query"] == "foo=bar"
    assert http_span["attributes"]["url.fragment"] == "frag"
    assert http_span["attributes"]["http.response.status_code"] == 200


@pytest.mark.asyncio
async def test_http_url_attributes_async(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/?foo=bar#frag"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["url.full"] == "http://example.com/?foo=bar#frag"
    assert http_span["attributes"]["url.query"] == "foo=bar"
    assert http_span["attributes"]["url.fragment"] == "frag"
    assert http_span["attributes"]["http.response.status_code"] == 200


def test_http_url_attributes_no_query_or_fragment_sync(
    sentry_init, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["url.full"] == "http://example.com/"
    assert "url.query" not in http_span["attributes"]
    assert "url.fragment" not in http_span["attributes"]
    assert http_span["attributes"]["http.response.status_code"] == 200


@pytest.mark.asyncio
async def test_http_url_attributes_no_query_or_fragment_async(
    sentry_init, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["url.full"] == "http://example.com/"
    assert "url.query" not in http_span["attributes"]
    assert "url.fragment" not in http_span["attributes"]
    assert http_span["attributes"]["http.response.status_code"] == 200


def test_http_url_attributes_pii_disabled_sync(sentry_init, capture_items, httpx2_mock):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/?foo=bar#frag"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert "url.full" not in http_span["attributes"]
    assert "url.query" not in http_span["attributes"]
    assert "url.fragment" not in http_span["attributes"]
    assert http_span["attributes"]["http.response.status_code"] == 200


@pytest.mark.asyncio
async def test_http_url_attributes_pii_disabled_async(
    sentry_init, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/?foo=bar#frag"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert "url.full" not in http_span["attributes"]
    assert "url.query" not in http_span["attributes"]
    assert "url.fragment" not in http_span["attributes"]
    assert http_span["attributes"]["http.response.status_code"] == 200


@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
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
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            None,
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            None,
            id="data_collection_wins_over_send_default_pii",
        ),
    ],
)
def test_url_query_data_collection_sync(
    sentry_init, capture_items, httpx2_mock, init_kwargs, expected_query
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    if expected_query is None:
        assert "url.query" not in http_span["attributes"]
    else:
        assert http_span["attributes"]["url.query"] == expected_query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
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
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            None,
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            None,
            id="data_collection_wins_over_send_default_pii",
        ),
    ],
)
async def test_url_query_data_collection_async(
    sentry_init, capture_items, httpx2_mock, init_kwargs, expected_query
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    if expected_query is None:
        assert "url.query" not in http_span["attributes"]
    else:
        assert http_span["attributes"]["url.query"] == expected_query


@pytest.mark.parametrize(
    "init_kwargs, expected_url_full",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "http://example.com/?toy=tennisball&color=red&auth=%5BFiltered%5D#frag",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "http://example.com/?toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D#frag",
            id="data_collection_allowlist",
        ),
    ],
)
def test_url_full_reassembly_sync(
    sentry_init, capture_items, httpx2_mock, init_kwargs, expected_url_full
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["url.full"] == expected_url_full


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_url_full",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "http://example.com/?toy=tennisball&color=red&auth=%5BFiltered%5D#frag",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "http://example.com/?toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D#frag",
            id="data_collection_allowlist",
        ),
    ],
)
async def test_url_full_reassembly_async(
    sentry_init, capture_items, httpx2_mock, init_kwargs, expected_url_full
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["url.full"] == expected_url_full


@pytest.mark.parametrize(
    "init_kwargs, expected_url_full",
    [
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "http://example.com/#frag",
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            "http://example.com/#frag",
            id="data_collection_wins_over_send_default_pii",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            id="send_default_pii_false",
        ),
    ],
)
def test_url_query_params_off_keeps_bare_url_sync(
    sentry_init, capture_items, httpx2_mock, init_kwargs, expected_url_full
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="test"):
        httpx2.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "url.query" not in http_span["attributes"]

    if expected_url_full is None:
        assert "url.full" not in http_span["attributes"]
        assert "url.fragment" not in http_span["attributes"]
    else:
        assert http_span["attributes"]["url.full"] == expected_url_full
        assert http_span["attributes"]["url.fragment"] == "frag"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_url_full",
    [
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "http://example.com/#frag",
            id="data_collection_off",
        ),
        pytest.param(
            {
                "send_default_pii": True,
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                },
            },
            "http://example.com/#frag",
            id="data_collection_wins_over_send_default_pii",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            id="send_default_pii_false",
        ),
    ],
)
async def test_url_query_params_off_keeps_bare_url_async(
    sentry_init, capture_items, httpx2_mock, init_kwargs, expected_url_full
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    items = capture_items("span")

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx2.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "url.query" not in http_span["attributes"]

    if expected_url_full is None:
        assert "url.full" not in http_span["attributes"]
        assert "url.fragment" not in http_span["attributes"]
    else:
        assert http_span["attributes"]["url.full"] == expected_url_full
        assert http_span["attributes"]["url.fragment"] == "frag"


@pytest.mark.parametrize(
    "init_kwargs, expected_url, expected_query, expected_fragment",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "http://example.com/?toy=tennisball&color=red&auth=%5BFiltered%5D#frag",
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            "frag",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "http://example.com/?toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D#frag",
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            "frag",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "http://example.com/#frag",
            "",
            "frag",
            id="data_collection_off",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            None,
            None,
            id="send_default_pii_false",
        ),
    ],
)
def test_crumb_url_query_data_collection_sync(
    sentry_init,
    capture_events,
    httpx2_mock,
    init_kwargs,
    expected_url,
    expected_query,
    expected_fragment,
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
        **init_kwargs,
    )

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        httpx2.Client().get(url)
        capture_message("Testing!")

        (event,) = events

    crumb = event["breadcrumbs"]["values"][0]

    if expected_url is None:
        assert "url" not in crumb["data"]
        assert SPANDATA.HTTP_QUERY not in crumb["data"]
        assert SPANDATA.HTTP_FRAGMENT not in crumb["data"]
    else:
        assert crumb["data"]["url"] == expected_url
        assert crumb["data"][SPANDATA.HTTP_QUERY] == expected_query
        assert crumb["data"][SPANDATA.HTTP_FRAGMENT] == expected_fragment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_kwargs, expected_url, expected_query, expected_fragment",
    [
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "http://example.com/?toy=tennisball&color=red&auth=%5BFiltered%5D#frag",
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            "frag",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "http://example.com/?toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D#frag",
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            "frag",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            "http://example.com/#frag",
            "",
            "frag",
            id="data_collection_off",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            None,
            None,
            id="send_default_pii_false",
        ),
    ],
)
async def test_crumb_url_query_data_collection_async(
    sentry_init,
    capture_events,
    httpx2_mock,
    init_kwargs,
    expected_url,
    expected_query,
    expected_fragment,
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        trace_lifecycle="stream",
        **init_kwargs,
    )

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        await httpx2.AsyncClient().get(url)
        capture_message("Testing!")

        (event,) = events

    crumb = event["breadcrumbs"]["values"][0]

    if expected_url is None:
        assert "url" not in crumb["data"]
        assert SPANDATA.HTTP_QUERY not in crumb["data"]
        assert SPANDATA.HTTP_FRAGMENT not in crumb["data"]
    else:
        assert crumb["data"]["url"] == expected_url
        assert crumb["data"][SPANDATA.HTTP_QUERY] == expected_query
        assert crumb["data"][SPANDATA.HTTP_FRAGMENT] == expected_fragment


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(
    sentry_init, capture_events, capture_items, httpx2_mock
):
    httpx2_mock.add_response()

    sentry_init(
        integrations=[Httpx2Integration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        _experiments={"data_collection": {}},
    )

    items = capture_items("span")

    url = "http://example.com/?toy=tennisball&color=red&auth=secret#frag"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        with mock.patch(
            "sentry_sdk.integrations.httpx2.parse_url",
            side_effect=ValueError,
        ):
            httpx2.Client().get(url)

        capture_message("Testing!")

        (event,) = events

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "url.full" not in http_span["attributes"]
    assert "url.query" not in http_span["attributes"]
    assert "url.fragment" not in http_span["attributes"]

    crumb = event["breadcrumbs"]["values"][0]

    assert "url" not in crumb["data"]
    assert SPANDATA.HTTP_QUERY not in crumb["data"]
    assert SPANDATA.HTTP_FRAGMENT not in crumb["data"]

import os
from unittest import mock

import httpx
import pytest

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import MATCH_ALL, OP, SPANDATA
from sentry_sdk.integrations.httpx import HttpxIntegration
from tests.conftest import ApproxDict


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_crumb_capture_and_hint_sync(
    sentry_init, capture_events, httpx_mock, send_default_pii
):
    httpx_mock.add_response()

    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(
        integrations=[HttpxIntegration()],
        before_breadcrumb=before_breadcrumb,
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = httpx.Client().get(url)

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
    sentry_init, capture_events, httpx_mock, send_default_pii
):
    httpx_mock.add_response()

    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(
        integrations=[HttpxIntegration()],
        before_breadcrumb=before_breadcrumb,
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = await httpx.AsyncClient().get(url)

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
    sentry_init, capture_events, httpx_mock, status_code, level, send_default_pii
):
    httpx_mock.add_response(status_code=status_code)

    sentry_init(
        integrations=[HttpxIntegration()],
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = httpx.Client().get(url)

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
    sentry_init, capture_events, httpx_mock, status_code, level, send_default_pii
):
    httpx_mock.add_response(status_code=status_code)

    sentry_init(
        integrations=[HttpxIntegration()],
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="segment"):
        events = capture_events()

        response = await httpx.AsyncClient().get(url)

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


def test_outgoing_trace_headers_legacy_sync(sentry_init, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
    )

    url = "http://example.com/"

    with start_transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="01234567890123456789012345678901",
    ) as transaction:
        response = httpx.Client().get(url)

        request_span = transaction._span_recorder.spans[-1]
        assert response.request.headers[
            "sentry-trace"
        ] == "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=request_span.span_id,
            sampled=1,
        )


@pytest.mark.asyncio
async def test_outgoing_trace_headers_legacy_async(sentry_init, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
    )

    url = "http://example.com/"

    with start_transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="01234567890123456789012345678901",
    ) as transaction:
        response = await httpx.AsyncClient().get(url)

        request_span = transaction._span_recorder.spans[-1]
        assert response.request.headers[
            "sentry-trace"
        ] == "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=request_span.span_id,
            sampled=1,
        )


def test_outgoing_trace_headers_append_to_baggage_legacy_sync(
    sentry_init,
    httpx_mock,
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    url = "http://example.com/"

    # patch random.randrange to return a predictable sample_rand value
    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with start_transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="01234567890123456789012345678901",
        ) as transaction:
            response = httpx.Client().get(url, headers={"baGGage": "custom=data"})

            request_span = transaction._span_recorder.spans[-1]
            assert response.request.headers[
                "sentry-trace"
            ] == "{trace_id}-{parent_span_id}-{sampled}".format(
                trace_id=transaction.trace_id,
                parent_span_id=request_span.span_id,
                sampled=1,
            )
            assert (
                response.request.headers["baggage"]
                == "custom=data,sentry-trace_id=01234567890123456789012345678901,sentry-sample_rand=0.500000,sentry-environment=production,sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,sentry-transaction=/interactions/other-dogs/new-dog,sentry-sample_rate=1.0,sentry-sampled=true"
            )


@pytest.mark.asyncio
async def test_outgoing_trace_headers_append_to_baggage_legacy_async(
    sentry_init,
    httpx_mock,
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    url = "http://example.com/"

    # patch random.randrange to return a predictable sample_rand value
    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with start_transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            trace_id="01234567890123456789012345678901",
        ) as transaction:
            response = await httpx.AsyncClient().get(
                url, headers={"baGGage": "custom=data"}
            )

            request_span = transaction._span_recorder.spans[-1]
            assert response.request.headers[
                "sentry-trace"
            ] == "{trace_id}-{parent_span_id}-{sampled}".format(
                trace_id=transaction.trace_id,
                parent_span_id=request_span.span_id,
                sampled=1,
            )
            assert (
                response.request.headers["baggage"]
                == "custom=data,sentry-trace_id=01234567890123456789012345678901,sentry-sample_rand=0.500000,sentry-environment=production,sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,sentry-transaction=/interactions/other-dogs/new-dog,sentry-sample_rate=1.0,sentry-sampled=true"
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
    httpx_mock,
    trace_propagation_targets,
    url,
    trace_propagated,
):
    httpx_mock.add_response()

    sentry_init(
        release="test",
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
    )

    with sentry_sdk.start_transaction():
        httpx.Client().get(url)

    request_headers = httpx_mock.get_request().headers

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
    httpx_mock,
    trace_propagation_targets,
    url,
    trace_propagated,
):
    httpx_mock.add_response()

    sentry_init(
        release="test",
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
    )

    with sentry_sdk.start_transaction():
        await httpx.AsyncClient().get(url)

    request_headers = httpx_mock.get_request().headers

    if trace_propagated:
        assert "sentry-trace" in request_headers
    else:
        assert "sentry-trace" not in request_headers


def test_do_not_propagate_outside_transaction(sentry_init, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        trace_propagation_targets=[MATCH_ALL],
        integrations=[HttpxIntegration()],
    )

    httpx_client = httpx.Client()
    httpx_client.get("http://example.com/")

    request_headers = httpx_mock.get_request().headers
    assert "sentry-trace" not in request_headers


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(sentry_init, capture_events, httpx_mock):
    httpx_mock.add_response()

    sentry_init(integrations=[HttpxIntegration()])

    httpx_client = httpx.Client()
    url = "http://example.com"

    events = capture_events()
    with mock.patch(
        "sentry_sdk.integrations.httpx.parse_url",
        side_effect=ValueError,
    ):
        response = httpx_client.get(url)

    assert response.status_code == 200
    capture_message("Testing!")

    (event,) = events
    assert event["breadcrumbs"]["values"][0]["data"] == ApproxDict(
        {
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            # no url related data
            "reason": "OK",
        }
    )

    assert "url" not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_FRAGMENT not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_QUERY not in event["breadcrumbs"]["values"][0]["data"]


def test_request_source_disabled_legacy_sync(sentry_init, capture_events, httpx_mock):
    httpx_mock.add_response()
    sentry_options = {
        "integrations": [HttpxIntegration()],
        "traces_sample_rate": 1.0,
        "enable_http_request_source": False,
        "http_request_source_threshold_ms": 0,
    }

    sentry_init(**sentry_options)

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        httpx.Client().get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.asyncio
async def test_request_source_disabled_legacy_async(
    sentry_init, capture_events, httpx_mock
):
    httpx_mock.add_response()
    sentry_options = {
        "integrations": [HttpxIntegration()],
        "traces_sample_rate": 1.0,
        "enable_http_request_source": False,
        "http_request_source_threshold_ms": 0,
    }

    sentry_init(**sentry_options)

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        await httpx.AsyncClient().get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize("enable_http_request_source", [None, True])
def test_request_source_enabled_legacy_sync(
    sentry_init,
    capture_events,
    enable_http_request_source,
    httpx_mock,
):
    httpx_mock.add_response()
    sentry_options = {
        "integrations": [HttpxIntegration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        httpx.Client().get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_http_request_source", [None, True])
async def test_request_source_enabled_legacy_async(
    sentry_init,
    capture_events,
    enable_http_request_source,
    httpx_mock,
):
    httpx_mock.add_response()
    sentry_options = {
        "integrations": [HttpxIntegration()],
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        await httpx.AsyncClient().get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data


def test_request_source_legacy_sync(sentry_init, capture_events, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        httpx.Client().get(url)

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
    assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.httpx.test_httpx"
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_request_source_legacy_sync"


@pytest.mark.asyncio
async def test_request_source_legacy_async(sentry_init, capture_events, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        await httpx.AsyncClient().get(url)

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
    assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.httpx.test_httpx"
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_request_source_legacy_async"


def test_request_source_with_module_in_search_path_legacy_sync(
    sentry_init, capture_events, httpx_mock
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    httpx_mock.add_response()
    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        from httpx_helpers.helpers import get_request_with_client

        get_request_with_client(httpx.Client(), url)

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
    assert data.get(SPANDATA.CODE_NAMESPACE) == "httpx_helpers.helpers"
    assert data.get(SPANDATA.CODE_FILEPATH) == "httpx_helpers/helpers.py"

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "get_request_with_client"


@pytest.mark.asyncio
async def test_request_source_with_module_in_search_path_legacy_async(
    sentry_init, capture_events, httpx_mock
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    httpx_mock.add_response()
    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        from httpx_helpers.helpers import async_get_request_with_client

        await async_get_request_with_client(httpx.AsyncClient(), url)

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
    assert data.get(SPANDATA.CODE_NAMESPACE) == "httpx_helpers.helpers"
    assert data.get(SPANDATA.CODE_FILEPATH) == "httpx_helpers/helpers.py"

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "async_get_request_with_client"


def test_no_request_source_if_duration_too_short_legacy_sync(
    sentry_init, capture_events, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold so high no real request will ever exceed it
        http_request_source_threshold_ms=9999999,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        httpx.Client().get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.asyncio
async def test_no_request_source_if_duration_too_short_legacy_async(
    sentry_init, capture_events, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold so high no real request will ever exceed it
        http_request_source_threshold_ms=9999999,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        await httpx.AsyncClient().get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


def test_request_source_if_duration_over_threshold_legacy_sync(
    sentry_init, capture_events, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold is low so any request will exceed it
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        httpx.Client().get(url)

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
    assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.httpx.test_httpx"
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert (
        data.get(SPANDATA.CODE_FUNCTION)
        == "test_request_source_if_duration_over_threshold_legacy_sync"
    )


@pytest.mark.asyncio
async def test_request_source_if_duration_over_threshold_legacy_async(
    sentry_init, capture_events, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold is low so any request will exceed it
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        await httpx.AsyncClient().get(url)

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
    assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.httpx.test_httpx"
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert (
        data.get(SPANDATA.CODE_FUNCTION)
        == "test_request_source_if_duration_over_threshold_legacy_async"
    )


def test_span_origin_legacy_sync(sentry_init, capture_events, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        httpx.Client().get(url)

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.http.httpx"


@pytest.mark.asyncio
async def test_span_origin_legacy_async(sentry_init, capture_events, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        await httpx.AsyncClient().get(url)

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.http.httpx"


def _get_http_client_span(items):
    return next(
        item.payload
        for item in items
        if item.payload.get("attributes", {}).get("sentry.op") == OP.HTTP_CLIENT
    )


def test_outgoing_trace_headers_span_streaming_sync(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        response = httpx.Client().get(url)

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
async def test_outgoing_trace_headers_span_streaming_async(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        response = await httpx.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert response.request.headers[
        "sentry-trace"
    ] == "{trace_id}-{span_id}-{sampled}".format(
        trace_id=http_span["trace_id"],
        span_id=http_span["span_id"],
        sampled=1,
    )


def test_outgoing_trace_headers_append_to_baggage_span_streaming_sync(
    sentry_init,
    capture_items,
    httpx_mock,
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with sentry_sdk.traces.start_span(name="test"):
            response = httpx.Client().get(url, headers={"baGGage": "custom=data"})

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    baggage = response.request.headers["baggage"]
    assert baggage.startswith("custom=data,")
    assert f"sentry-trace_id={http_span['trace_id']}" in baggage
    assert "sentry-sample_rand=0.500000" in baggage
    assert "sentry-sampled=true" in baggage


@pytest.mark.asyncio
async def test_outgoing_trace_headers_append_to_baggage_span_streaming_async(
    sentry_init,
    capture_items,
    httpx_mock,
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    items = capture_items("span")

    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=500000):
        with sentry_sdk.traces.start_span(name="test"):
            response = await httpx.AsyncClient().get(
                url, headers={"baGGage": "custom=data"}
            )

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    baggage = response.request.headers["baggage"]
    assert baggage.startswith("custom=data,")
    assert f"sentry-trace_id={http_span['trace_id']}" in baggage
    assert "sentry-sample_rand=0.500000" in baggage
    assert "sentry-sampled=true" in baggage


def test_outgoing_trace_headers_span_streaming_no_current_span(sentry_init, httpx_mock):
    """
    Even when there is no active span, trace propagation headers should still
    be attached to outgoing requests when span streaming is enabled.

    This is deliberately different from the legacy (transaction-based) approach,
    which does not propagate outside of a transaction (see
    ``test_do_not_propagate_outside_transaction``). The streamed approach
    propagates from the current scope's propagation context regardless of
    whether a span is active.
    """
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        trace_propagation_targets=[MATCH_ALL],
        integrations=[HttpxIntegration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    httpx_client = httpx.Client()

    # No start_span / start_transaction -> get_current_span() is None
    assert sentry_sdk.traces.get_current_span() is None

    response = httpx_client.get(url)

    assert response.status_code == 200

    # Trace is still propagated from the scope's propagation context
    request_headers = httpx_mock.get_request().headers
    assert "sentry-trace" in request_headers
    assert "baggage" in request_headers

    # The propagated headers describe a single, coherent trace: the trace_id in
    # sentry-trace matches the one carried in baggage.
    trace_id = request_headers["sentry-trace"].split("-")[0]
    assert f"sentry-trace_id={trace_id}" in request_headers["baggage"]


@pytest.mark.asyncio
async def test_outgoing_trace_headers_span_streaming_no_current_span_async(
    sentry_init, httpx_mock
):
    """
    The async client must match the sync client: trace propagation headers are
    attached to outgoing requests even when there is no active span and span
    streaming is enabled.
    """
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        trace_propagation_targets=[MATCH_ALL],
        integrations=[HttpxIntegration()],
        trace_lifecycle="stream",
    )

    url = "http://example.com/"

    httpx_client = httpx.AsyncClient()

    # No start_span / start_transaction -> get_current_span() is None
    assert sentry_sdk.traces.get_current_span() is None

    response = await httpx_client.get(url)

    assert response.status_code == 200

    # Trace is still propagated from the scope's propagation context
    request_headers = httpx_mock.get_request().headers
    assert "sentry-trace" in request_headers
    assert "baggage" in request_headers

    # The propagated headers describe a single, coherent trace: the trace_id in
    # sentry-trace matches the one carried in baggage.
    trace_id = request_headers["sentry-trace"].split("-")[0]
    assert f"sentry-trace_id={trace_id}" in request_headers["baggage"]


def test_request_source_disabled_span_streaming_sync(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


@pytest.mark.asyncio
async def test_request_source_disabled_span_streaming_async(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


@pytest.mark.parametrize("enable_http_request_source", [None, True])
def test_request_source_enabled_span_streaming_sync(
    sentry_init,
    capture_items,
    enable_http_request_source,
    httpx_mock,
):
    httpx_mock.add_response()

    sentry_options = {
        "integrations": [HttpxIntegration()],
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
        httpx.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_http_request_source", [None, True])
async def test_request_source_enabled_span_streaming_async(
    sentry_init,
    capture_items,
    enable_http_request_source,
    httpx_mock,
):
    httpx_mock.add_response()

    sentry_options = {
        "integrations": [HttpxIntegration()],
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
        await httpx.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]


def test_request_source_span_streaming_sync(sentry_init, capture_items, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx.Client().get(url)

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
        == "tests.integrations.httpx.test_httpx"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "test_request_source_span_streaming_sync"
    )


@pytest.mark.asyncio
async def test_request_source_span_streaming_async(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx.AsyncClient().get(url)

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
        == "tests.integrations.httpx.test_httpx"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "test_request_source_span_streaming_async"
    )


def test_request_source_with_module_in_search_path_span_streaming_sync(
    sentry_init, capture_items, httpx_mock
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        from httpx_helpers.helpers import get_request_with_client

        get_request_with_client(httpx.Client(), url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert http_span["attributes"][SPANDATA.CODE_NAMESPACE] == "httpx_helpers.helpers"
    assert http_span["attributes"]["code.file.path"] == "httpx_helpers/helpers.py"

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert http_span["attributes"][SPANDATA.CODE_FUNCTION] == "get_request_with_client"


@pytest.mark.asyncio
async def test_request_source_with_module_in_search_path_span_streaming_async(
    sentry_init, capture_items, httpx_mock
):
    """
    Test that request source is relative to the path of the module it ran in
    """
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        from httpx_helpers.helpers import async_get_request_with_client

        await async_get_request_with_client(httpx.AsyncClient(), url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE in http_span["attributes"]
    assert "code.file.path" in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION in http_span["attributes"]

    assert type(http_span["attributes"]["code.line.number"]) == int
    assert http_span["attributes"]["code.line.number"] > 0
    assert http_span["attributes"][SPANDATA.CODE_NAMESPACE] == "httpx_helpers.helpers"
    assert http_span["attributes"]["code.file.path"] == "httpx_helpers/helpers.py"

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "async_get_request_with_client"
    )


def test_no_request_source_if_duration_too_short_span_streaming_sync(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold so high no real request will ever exceed it
        http_request_source_threshold_ms=9999999,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


@pytest.mark.asyncio
async def test_no_request_source_if_duration_too_short_span_streaming_async(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold so high no real request will ever exceed it
        http_request_source_threshold_ms=9999999,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert "code.line.number" not in http_span["attributes"]
    assert SPANDATA.CODE_NAMESPACE not in http_span["attributes"]
    assert "code.file.path" not in http_span["attributes"]
    assert SPANDATA.CODE_FUNCTION not in http_span["attributes"]


def test_request_source_if_duration_over_threshold_span_streaming_sync(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold of 0 means any non-zero duration qualifies
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx.Client().get(url)

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
        == "tests.integrations.httpx.test_httpx"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "test_request_source_if_duration_over_threshold_span_streaming_sync"
    )


@pytest.mark.asyncio
async def test_request_source_if_duration_over_threshold_span_streaming_async(
    sentry_init, capture_items, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        # Threshold of 0 means any non-zero duration qualifies
        http_request_source_threshold_ms=0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx.AsyncClient().get(url)

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
        == "tests.integrations.httpx.test_httpx"
    )
    assert http_span["attributes"]["code.file.path"].endswith(
        "tests/integrations/httpx/test_httpx.py"
    )

    is_relative_path = http_span["attributes"]["code.file.path"][0] != os.sep
    assert is_relative_path

    assert (
        http_span["attributes"][SPANDATA.CODE_FUNCTION]
        == "test_request_source_if_duration_over_threshold_span_streaming_async"
    )


def test_span_origin_span_streaming_sync(sentry_init, capture_items, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["sentry.origin"] == "auto.http.httpx"


@pytest.mark.asyncio
async def test_span_origin_span_streaming_async(sentry_init, capture_items, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["sentry.origin"] == "auto.http.httpx"


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_http_url_attributes_span_streaming_sync(
    sentry_init, capture_items, httpx_mock, send_default_pii
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/?foo=bar#frag"

    with sentry_sdk.traces.start_span(name="test"):
        httpx.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["http.response.status_code"] == 200

    if send_default_pii:
        assert http_span["attributes"]["url.full"] == "http://example.com/?foo=bar#frag"
        assert http_span["attributes"]["url.query"] == "foo=bar"
        assert http_span["attributes"]["url.fragment"] == "frag"
    else:
        assert "url.full" not in http_span["attributes"]
        assert "url.query" not in http_span["attributes"]
        assert "url.fragment" not in http_span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_http_url_attributes_span_streaming_async(
    sentry_init, capture_items, httpx_mock, send_default_pii
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/?foo=bar#frag"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["http.response.status_code"] == 200

    if send_default_pii:
        assert http_span["attributes"]["url.full"] == "http://example.com/?foo=bar#frag"
        assert http_span["attributes"]["url.query"] == "foo=bar"
        assert http_span["attributes"]["url.fragment"] == "frag"
    else:
        assert "url.full" not in http_span["attributes"]
        assert "url.query" not in http_span["attributes"]
        assert "url.fragment" not in http_span["attributes"]


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_http_url_attributes_no_query_or_fragment_span_streaming_sync(
    sentry_init, capture_items, httpx_mock, send_default_pii
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        httpx.Client().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["http.response.status_code"] == 200
    assert "url.query" not in http_span["attributes"]
    assert "url.fragment" not in http_span["attributes"]

    if send_default_pii:
        assert http_span["attributes"]["url.full"] == "http://example.com/"
    else:
        assert "url.full" not in http_span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
async def test_http_url_attributes_no_query_or_fragment_span_streaming_async(
    sentry_init, capture_items, httpx_mock, send_default_pii
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    url = "http://example.com/"

    with sentry_sdk.traces.start_span(name="test"):
        await httpx.AsyncClient().get(url)

    sentry_sdk.flush()

    http_span = _get_http_client_span(items)

    assert http_span["attributes"]["http.request.method"] == "GET"
    assert http_span["attributes"]["http.response.status_code"] == 200
    assert "url.query" not in http_span["attributes"]
    assert "url.fragment" not in http_span["attributes"]

    if send_default_pii:
        assert http_span["attributes"]["url.full"] == "http://example.com/"
    else:
        assert "url.full" not in http_span["attributes"]

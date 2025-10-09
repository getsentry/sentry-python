import os
import datetime
import asyncio
from unittest import mock

import httpx
import pytest
from contextlib import contextmanager

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.httpx import HttpxIntegration
from tests.conftest import ApproxDict


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_crumb_capture_and_hint(sentry_init, capture_events, httpx_client, httpx_mock):
    httpx_mock.add_response()

    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[HttpxIntegration()], before_breadcrumb=before_breadcrumb)

    url = "http://example.com/"

    with start_transaction():
        events = capture_events()

        if asyncio.iscoroutinefunction(httpx_client.get):
            response = asyncio.get_event_loop().run_until_complete(
                httpx_client.get(url)
            )
        else:
            response = httpx_client.get(url)

        assert response.status_code == 200
        capture_message("Testing!")

        (event,) = events

        crumb = event["breadcrumbs"]["values"][0]
        assert crumb["type"] == "http"
        assert crumb["category"] == "httplib"
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


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
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
def test_crumb_capture_client_error(
    sentry_init, capture_events, httpx_client, httpx_mock, status_code, level
):
    httpx_mock.add_response(status_code=status_code)

    sentry_init(integrations=[HttpxIntegration()])

    url = "http://example.com/"

    with start_transaction():
        events = capture_events()

        if asyncio.iscoroutinefunction(httpx_client.get):
            response = asyncio.get_event_loop().run_until_complete(
                httpx_client.get(url)
            )
        else:
            response = httpx_client.get(url)

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

        assert crumb["data"] == ApproxDict(
            {
                "url": url,
                SPANDATA.HTTP_METHOD: "GET",
                SPANDATA.HTTP_FRAGMENT: "",
                SPANDATA.HTTP_QUERY: "",
                SPANDATA.HTTP_STATUS_CODE: status_code,
            }
        )


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_outgoing_trace_headers(sentry_init, httpx_client, httpx_mock):
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
        if asyncio.iscoroutinefunction(httpx_client.get):
            response = asyncio.get_event_loop().run_until_complete(
                httpx_client.get(url)
            )
        else:
            response = httpx_client.get(url)

        request_span = transaction._span_recorder.spans[-1]
        assert response.request.headers[
            "sentry-trace"
        ] == "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=request_span.span_id,
            sampled=1,
        )


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_outgoing_trace_headers_append_to_baggage(
    sentry_init,
    httpx_client,
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
            if asyncio.iscoroutinefunction(httpx_client.get):
                response = asyncio.get_event_loop().run_until_complete(
                    httpx_client.get(url, headers={"baGGage": "custom=data"})
                )
            else:
                response = httpx_client.get(url, headers={"baGGage": "custom=data"})

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
    "httpx_client,trace_propagation_targets,url,trace_propagated",
    [
        [
            httpx.Client(),
            None,
            "https://example.com/",
            False,
        ],
        [
            httpx.Client(),
            [],
            "https://example.com/",
            False,
        ],
        [
            httpx.Client(),
            [MATCH_ALL],
            "https://example.com/",
            True,
        ],
        [
            httpx.Client(),
            ["https://example.com/"],
            "https://example.com/",
            True,
        ],
        [
            httpx.Client(),
            ["https://example.com/"],
            "https://example.com",
            False,
        ],
        [
            httpx.Client(),
            ["https://example.com"],
            "https://example.com",
            True,
        ],
        [
            httpx.Client(),
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://example.net",
            False,
        ],
        [
            httpx.Client(),
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net",
            True,
        ],
        [
            httpx.Client(),
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net/some/thing",
            True,
        ],
        [
            httpx.AsyncClient(),
            None,
            "https://example.com/",
            False,
        ],
        [
            httpx.AsyncClient(),
            [],
            "https://example.com/",
            False,
        ],
        [
            httpx.AsyncClient(),
            [MATCH_ALL],
            "https://example.com/",
            True,
        ],
        [
            httpx.AsyncClient(),
            ["https://example.com/"],
            "https://example.com/",
            True,
        ],
        [
            httpx.AsyncClient(),
            ["https://example.com/"],
            "https://example.com",
            False,
        ],
        [
            httpx.AsyncClient(),
            ["https://example.com"],
            "https://example.com",
            True,
        ],
        [
            httpx.AsyncClient(),
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://example.net",
            False,
        ],
        [
            httpx.AsyncClient(),
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net",
            True,
        ],
        [
            httpx.AsyncClient(),
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net/some/thing",
            True,
        ],
    ],
)
def test_option_trace_propagation_targets(
    sentry_init,
    httpx_client,
    httpx_mock,  # this comes from pytest-httpx
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

    # Must be in a transaction to propagate headers
    with sentry_sdk.start_transaction():
        if asyncio.iscoroutinefunction(httpx_client.get):
            asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
        else:
            httpx_client.get(url)

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
            "reason": "OK",
            # no url related data
        }
    )

    assert "url" not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_FRAGMENT not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_QUERY not in event["breadcrumbs"]["values"][0]["data"]


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_request_source_disabled(sentry_init, capture_events, httpx_client, httpx_mock):
    httpx_mock.add_response()
    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        if asyncio.iscoroutinefunction(httpx_client.get):
            asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
        else:
            httpx_client.get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize("enable_http_request_source", [None, True])
@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_request_source_enabled(
    sentry_init, capture_events, enable_http_request_source, httpx_client, httpx_mock
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
        if asyncio.iscoroutinefunction(httpx_client.get):
            asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
        else:
            httpx_client.get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_request_source(sentry_init, capture_events, httpx_client, httpx_mock):
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
        if asyncio.iscoroutinefunction(httpx_client.get):
            asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
        else:
            httpx_client.get(url)

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

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_request_source"


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_request_source_with_module_in_search_path(
    sentry_init, capture_events, httpx_client, httpx_mock
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
        if asyncio.iscoroutinefunction(httpx_client.get):
            from httpx_helpers.helpers import async_get_request_with_client

            asyncio.get_event_loop().run_until_complete(
                async_get_request_with_client(httpx_client, url)
            )
        else:
            from httpx_helpers.helpers import get_request_with_client

            get_request_with_client(httpx_client, url)

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

    if asyncio.iscoroutinefunction(httpx_client.get):
        assert data.get(SPANDATA.CODE_FUNCTION) == "async_get_request_with_client"
    else:
        assert data.get(SPANDATA.CODE_FUNCTION) == "get_request_with_client"


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_no_request_source_if_duration_too_short(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):

        @contextmanager
        def fake_start_span(*args, **kwargs):
            with sentry_sdk.start_span(*args, **kwargs) as span:
                pass
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
            yield span

        with mock.patch(
            "sentry_sdk.integrations.httpx.start_span",
            fake_start_span,
        ):
            if asyncio.iscoroutinefunction(httpx_client.get):
                asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
            else:
                httpx_client.get(url)

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_request_source_if_duration_over_threshold(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):

        @contextmanager
        def fake_start_span(*args, **kwargs):
            with sentry_sdk.start_span(*args, **kwargs) as span:
                pass
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)
            yield span

        with mock.patch(
            "sentry_sdk.integrations.httpx.start_span",
            fake_start_span,
        ):
            if asyncio.iscoroutinefunction(httpx_client.get):
                asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
            else:
                httpx_client.get(url)

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
        == "test_query_source_if_duration_over_threshold"
    )


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_span_origin(sentry_init, capture_events, httpx_client, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_transaction(name="test_transaction"):
        if asyncio.iscoroutinefunction(httpx_client.get):
            asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
        else:
            httpx_client.get(url)

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.http.httpx"

import asyncio

import pytest
import httpx
import responses

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.httpx import HttpxIntegration
from tests.conftest import ApproxDict

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_crumb_capture_and_hint(sentry_init, capture_events, httpx_client):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[HttpxIntegration()], before_breadcrumb=before_breadcrumb)

    url = "http://example.com/"
    responses.add(responses.GET, url, status=200)

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
def test_outgoing_trace_headers(sentry_init, httpx_client):
    sentry_init(traces_sample_rate=1.0, integrations=[HttpxIntegration()])

    url = "http://example.com/"
    responses.add(responses.GET, url, status=200)

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
def test_outgoing_trace_headers_append_to_baggage(sentry_init, httpx_client):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    url = "http://example.com/"
    responses.add(responses.GET, url, status=200)

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
            == "custom=data,sentry-trace_id=01234567890123456789012345678901,sentry-environment=production,sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,sentry-transaction=/interactions/other-dogs/new-dog,sentry-sample_rate=1.0,sentry-sampled=true"
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

    if asyncio.iscoroutinefunction(httpx_client.get):
        asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
    else:
        httpx_client.get(url)

    request_headers = httpx_mock.get_request().headers

    if trace_propagated:
        assert "sentry-trace" in request_headers
    else:
        assert "sentry-trace" not in request_headers


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(sentry_init, capture_events):
    sentry_init(integrations=[HttpxIntegration()])

    httpx_client = httpx.Client()
    url = "http://example.com"
    responses.add(responses.GET, url, status=200)

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

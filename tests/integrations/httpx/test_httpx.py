import asyncio
from unittest import mock

import httpx
import pytest

import sentry_sdk
from sentry_sdk import capture_message, start_span
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.httpx import HttpxIntegration
from tests.conftest import ApproxDict, SortedBaggage


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

    with start_span():
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

    with start_span(name="crumbs"):
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
def test_outgoing_trace_headers(
    sentry_init, httpx_client, capture_envelopes, httpx_mock
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
    )

    envelopes = capture_envelopes()

    url = "http://example.com/"

    with start_span(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
    ):
        if asyncio.iscoroutinefunction(httpx_client.get):
            response = asyncio.get_event_loop().run_until_complete(
                httpx_client.get(url)
            )
        else:
            response = httpx_client.get(url)

    (envelope,) = envelopes
    transaction = envelope.get_transaction_event()
    request_span = transaction["spans"][-1]

    assert response.request.headers[
        "sentry-trace"
    ] == "{trace_id}-{parent_span_id}-{sampled}".format(
        trace_id=transaction["contexts"]["trace"]["trace_id"],
        parent_span_id=request_span["span_id"],
        sampled=1,
    )


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_outgoing_trace_headers_append_to_baggage(
    sentry_init,
    httpx_client,
    capture_envelopes,
    httpx_mock,
):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    envelopes = capture_envelopes()

    url = "http://example.com/"

    with start_span(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
    ):
        if asyncio.iscoroutinefunction(httpx_client.get):
            response = asyncio.get_event_loop().run_until_complete(
                httpx_client.get(url, headers={"baGGage": "custom=data"})
            )
        else:
            response = httpx_client.get(url, headers={"baGGage": "custom=data"})

    (envelope,) = envelopes
    transaction = envelope.get_transaction_event()
    request_span = transaction["spans"][-1]
    trace_id = transaction["contexts"]["trace"]["trace_id"]

    assert response.request.headers[
        "sentry-trace"
    ] == "{trace_id}-{parent_span_id}-{sampled}".format(
        trace_id=trace_id,
        parent_span_id=request_span["span_id"],
        sampled=1,
    )
    assert response.request.headers["baggage"] == SortedBaggage(
        f"custom=data,sentry-trace_id={trace_id},sentry-environment=production,sentry-release=d08ebdb9309e1b004c6f52202de58a09c2268e42,sentry-transaction=/interactions/other-dogs/new-dog,sentry-sample_rate=1.0,sentry-sampled=true"  # noqa: E231
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

    with sentry_sdk.start_span():  # Must be in a root span to propagate headers
        if asyncio.iscoroutinefunction(httpx_client.get):
            asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
        else:
            httpx_client.get(url)

    request_headers = httpx_mock.get_request().headers

    if trace_propagated:
        assert "sentry-trace" in request_headers
    else:
        assert "sentry-trace" not in request_headers


def test_propagates_twp_outside_root_span(sentry_init, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        traces_sample_rate=1.0,
        trace_propagation_targets=[MATCH_ALL],
        integrations=[HttpxIntegration()],
    )

    httpx_client = httpx.Client()
    httpx_client.get("http://example.com/")

    request_headers = httpx_mock.get_request().headers
    assert "sentry-trace" in request_headers
    assert request_headers["sentry-trace"] == sentry_sdk.get_traceparent()


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
def test_span_origin(sentry_init, capture_events, httpx_client, httpx_mock):
    httpx_mock.add_response()

    sentry_init(
        integrations=[HttpxIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    url = "http://example.com/"

    with start_span(name="test_root_span"):
        if asyncio.iscoroutinefunction(httpx_client.get):
            asyncio.get_event_loop().run_until_complete(httpx_client.get(url))
        else:
            httpx_client.get(url)

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.http.httpx"

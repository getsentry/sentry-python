import asyncio

import pytest
import httpx
from textwrap import dedent

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.httpx import HttpxIntegration

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

try:
    from urllib.parse import parse_qsl
except ImportError:
    from urlparse import parse_qsl  # type: ignore


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_crumb_capture_and_hint(sentry_init, capture_events, httpx_client, httpx_mock):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[HttpxIntegration()], before_breadcrumb=before_breadcrumb)

    url = "http://example.com/"
    httpx_mock.add_response()

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
        assert crumb["data"] == {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
            "extra": "foo",
        }


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_outgoing_trace_headers(sentry_init, httpx_client, httpx_mock):
    sentry_init(traces_sample_rate=1.0, integrations=[HttpxIntegration()])

    url = "http://example.com/"
    httpx_mock.add_response()

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
    sentry_init, httpx_client, httpx_mock
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[HttpxIntegration()],
        release="d08ebdb9309e1b004c6f52202de58a09c2268e42",
    )

    url = "http://example.com/"
    httpx_mock.add_response()

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
def test_omit_url_data_if_parsing_fails(sentry_init, capture_events, httpx_mock):
    sentry_init(integrations=[HttpxIntegration()])

    httpx_client = httpx.Client()
    url = "http://example.com"
    httpx_mock.add_response()

    events = capture_events()
    with mock.patch(
        "sentry_sdk.integrations.httpx.parse_url",
        side_effect=ValueError,
    ):
        response = httpx_client.get(url)

    assert response.status_code == 200
    capture_message("Testing!")

    (event,) = events
    assert event["breadcrumbs"]["values"][0]["data"] == {
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: 200,
        "reason": "OK",
        # no url related data
    }


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_graphql_get_client_error_captured(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    sentry_init(send_default_pii=True, integrations=[HttpxIntegration()])

    url = "http://example.com/graphql"
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "some error",
                "locations": [{"line": 2, "column": 3}],
                "path": ["user"],
            }
        ],
    }
    params = {"query": "query QueryName {user{name}}"}

    httpx_mock.add_response(method="GET", json=graphql_response)

    events = capture_events()

    if asyncio.iscoroutinefunction(httpx_client.get):
        response = asyncio.get_event_loop().run_until_complete(
            httpx_client.get(url, params=params)
        )
    else:
        response = httpx_client.get(url, params=params)

    assert response.status_code == 200
    assert response.json() == graphql_response

    (event,) = events

    assert event["request"]["url"] == url
    assert event["request"]["method"] == "GET"
    assert dict(parse_qsl(event["request"]["query_string"])) == params
    assert "data" not in event["request"]
    assert event["contexts"]["response"]["data"] == graphql_response

    assert event["request"]["api_target"] == "graphql"
    assert event["fingerprint"] == ["QueryName", "query", 200]
    assert (
        event["exception"]["values"][0]["value"]
        == "GraphQL request failed, name: QueryName, type: query"
    )


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_graphql_post_client_error_captured(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    sentry_init(send_default_pii=True, integrations=[HttpxIntegration()])

    url = "http://example.com/graphql"
    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "already have too many pets",
                "locations": [{"line": 1, "column": 1}],
            }
        ],
    }
    httpx_mock.add_response(method="POST", json=graphql_response)

    events = capture_events()

    if asyncio.iscoroutinefunction(httpx_client.post):
        response = asyncio.get_event_loop().run_until_complete(
            httpx_client.post(url, json=graphql_request)
        )
    else:
        response = httpx_client.post(url, json=graphql_request)

    assert response.status_code == 200
    assert response.json() == graphql_response

    (event,) = events

    assert event["request"]["url"] == url
    assert event["request"]["method"] == "POST"
    assert event["request"]["query_string"] == ""
    assert event["request"]["data"] == graphql_request
    assert event["contexts"]["response"]["data"] == graphql_response

    assert event["request"]["api_target"] == "graphql"
    assert event["fingerprint"] == ["AddPet", "mutation", 200]
    assert (
        event["exception"]["values"][0]["value"]
        == "GraphQL request failed, name: AddPet, type: mutation"
    )


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_graphql_get_client_no_errors_returned(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    sentry_init(send_default_pii=True, integrations=[HttpxIntegration()])

    url = "http://example.com/graphql"
    graphql_response = {
        "data": None,
    }
    params = {"query": "query QueryName {user{name}}"}

    httpx_mock.add_response(method="GET", json=graphql_response)

    events = capture_events()

    if asyncio.iscoroutinefunction(httpx_client.get):
        response = asyncio.get_event_loop().run_until_complete(
            httpx_client.get(url, params=params)
        )
    else:
        response = httpx_client.get(url, params=params)

    assert response.status_code == 200
    assert response.json() == graphql_response

    assert not events


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_graphql_post_client_no_errors_returned(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    sentry_init(send_default_pii=True, integrations=[HttpxIntegration()])

    url = "http://example.com/graphql"
    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }
    graphql_response = {
        "data": None,
    }
    httpx_mock.add_response(method="POST", json=graphql_response)

    events = capture_events()

    if asyncio.iscoroutinefunction(httpx_client.post):
        response = asyncio.get_event_loop().run_until_complete(
            httpx_client.post(url, json=graphql_request)
        )
    else:
        response = httpx_client.post(url, json=graphql_request)

    assert response.status_code == 200
    assert response.json() == graphql_response

    assert not events


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_graphql_no_get_errors_if_option_is_off(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    sentry_init(
        send_default_pii=True,
        integrations=[HttpxIntegration(capture_graphql_errors=False)],
    )

    url = "http://example.com/graphql"
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "some error",
                "locations": [{"line": 2, "column": 3}],
                "path": ["user"],
            }
        ],
    }
    params = {"query": "query QueryName {user{name}}"}

    httpx_mock.add_response(method="GET", json=graphql_response)

    events = capture_events()

    if asyncio.iscoroutinefunction(httpx_client.get):
        response = asyncio.get_event_loop().run_until_complete(
            httpx_client.get(url, params=params)
        )
    else:
        response = httpx_client.get(url, params=params)

    assert response.status_code == 200
    assert response.json() == graphql_response

    assert not events


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_graphql_no_post_errors_if_option_is_off(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    sentry_init(
        send_default_pii=True,
        integrations=[HttpxIntegration(capture_graphql_errors=False)],
    )

    url = "http://example.com/graphql"
    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "already have too many pets",
                "locations": [{"line": 1, "column": 1}],
            }
        ],
    }
    httpx_mock.add_response(method="POST", json=graphql_response)

    events = capture_events()

    if asyncio.iscoroutinefunction(httpx_client.post):
        response = asyncio.get_event_loop().run_until_complete(
            httpx_client.post(url, json=graphql_request)
        )
    else:
        response = httpx_client.post(url, json=graphql_request)

    assert response.status_code == 200
    assert response.json() == graphql_response

    assert not events


@pytest.mark.parametrize(
    "httpx_client",
    (httpx.Client(), httpx.AsyncClient()),
)
def test_graphql_non_json_response(
    sentry_init, capture_events, httpx_client, httpx_mock
):
    sentry_init(
        send_default_pii=True,
        integrations=[HttpxIntegration()],
    )

    url = "http://example.com/graphql"
    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }
    httpx_mock.add_response(method="POST")

    events = capture_events()

    if asyncio.iscoroutinefunction(httpx_client.post):
        response = asyncio.get_event_loop().run_until_complete(
            httpx_client.post(url, json=graphql_request)
        )
    else:
        response = httpx_client.post(url, json=graphql_request)

    assert response.status_code == 200

    assert not events

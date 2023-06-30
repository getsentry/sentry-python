import pytest
import responses
from textwrap import dedent

requests = pytest.importorskip("requests")

from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def test_crumb_capture(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])

    url = "http://example.com/"
    responses.add(responses.GET, url, status=200)

    events = capture_events()

    response = requests.get(url)
    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == {
        "url": url,
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_FRAGMENT: "",
        SPANDATA.HTTP_QUERY: "",
        SPANDATA.HTTP_STATUS_CODE: response.status_code,
        "reason": response.reason,
    }


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])

    url = "https://example.com"
    responses.add(responses.GET, url, status=200)

    events = capture_events()

    with mock.patch(
        "sentry_sdk.integrations.stdlib.parse_url",
        side_effect=ValueError,
    ):
        response = requests.get(url)

    capture_message("Testing!")

    (event,) = events
    assert event["breadcrumbs"]["values"][0]["data"] == {
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: response.status_code,
        "reason": response.reason,
        # no url related data
    }


def test_graphql_get_client_error_captured(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])

    url = "http://example.com/graphql"
    response = {
        "data": None,
        "errors": [
            {
                "message": "some error",
                "locations": [{"line": 2, "column": 3}],
                "path": ["user"],
            }
        ],
    }
    responses.add(responses.GET, url, status=200, json=response)

    events = capture_events()

    requests.get(url, params={"query": "query QueryName {user{name}}"})

    (event,) = events

    assert event["request"]["url"] == url
    assert event["request"]["method"] == "GET"
    assert (
        event["request"]["query_string"]
        == "query=query%20QueryName%20%7Buser%7Bname%7D%7D"
    )
    assert "data" not in event["request"]
    assert event["contexts"]["response"]["data"] == response

    assert event["request"]["api_target"] == "graphql"
    assert event["fingerprint"] == ["QueryName", "query", 200]
    assert (
        event["exception"]["values"][0]["value"]
        == "GraphQL request failed, name: QueryName, type: query"
    )


def test_graphql_post_client_error_captured(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])

    url = "http://example.com/graphql"
    request = {
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
    response = {
        "data": None,
        "errors": [
            {
                "message": "already have too many pets",
                "locations": [{"line": 1, "column": 1}],
            }
        ],
    }
    responses.add(responses.POST, url, status=200, json=response)

    events = capture_events()

    requests.post(url, json=request)

    (event,) = events

    assert event["request"]["url"] == url
    assert event["request"]["method"] == "POST"
    assert event["request"]["query_string"] == ""
    assert event["request"]["data"] == request
    assert event["contexts"]["response"]["data"] == response

    assert event["request"]["api_target"] == "graphql"
    assert event["fingerprint"] == ["AddPet", "mutation", 200]
    assert (
        event["exception"]["values"][0]["value"]
        == "GraphQL request failed, name: AddPet, type: mutation"
    )

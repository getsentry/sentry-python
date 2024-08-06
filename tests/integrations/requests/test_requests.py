from unittest import mock

import pytest
import requests
import responses

from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration


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
    assert (
        crumb["data"].items()
        >= {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
            SPANDATA.HTTP_STATUS_CODE: response.status_code,
            "reason": response.reason,
        }.items()
    )


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
    assert (
        event["breadcrumbs"]["values"][0]["data"].items()
        >= {
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: response.status_code,
            "reason": response.reason,
            # no url related data
        }.items()
    )

    assert "url" not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_FRAGMENT not in event["breadcrumbs"]["values"][0]["data"]
    assert SPANDATA.HTTP_QUERY not in event["breadcrumbs"]["values"][0]["data"]

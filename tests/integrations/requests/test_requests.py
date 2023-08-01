import json
import pytest
import responses

requests = pytest.importorskip("requests")

from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration

from tests.conftest import MockServerRequestHandler, create_mock_http_server

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

PORT = create_mock_http_server()


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


def test_graphql_integration_doesnt_affect_responses(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])

    events = capture_events()

    msg = {"errors": [{"message": "some message"}]}

    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(msg).encode())

    with mock.patch.object(MockServerRequestHandler, "do_POST", do_POST):
        response = requests.post("http://localhost:{}".format(PORT) + "/graphql")

    assert len(events) == 1
    assert response.json() == msg

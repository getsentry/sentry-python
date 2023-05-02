import pytest
import responses

requests = pytest.importorskip("requests")

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
    assert crumb["data"] == {
        "url": url,
        "method": "GET",
        SPANDATA.HTTP_FRAGMENT: "",
        SPANDATA.HTTP_QUERY: "",
        "status_code": response.status_code,
        "reason": response.reason,
    }

import pytest

requests = pytest.importorskip("requests")

from sentry_sdk import capture_message
from sentry_sdk.integrations.requests import RequestsIntegration


def test_crumb_capture(sentry_init, capture_events):
    sentry_init(integrations=[RequestsIntegration()])
    events = capture_events()

    response = requests.get("https://httpbin.org/status/418")
    assert response.status_code == 418
    capture_message("Testing!")

    event, = events
    crumb, = event["breadcrumbs"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "requests"
    assert crumb["data"] == {
        "url": "https://httpbin.org/status/418",
        "method": "GET",
        "status_code": 418,
        "reason": "I'M A TEAPOT",
    }

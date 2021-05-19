import asyncio

import httpx

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.httpx import HttpxIntegration


def test_crumb_capture_and_hint(sentry_init, capture_events):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[HttpxIntegration()], before_breadcrumb=before_breadcrumb)

    clients = (httpx.Client(), httpx.AsyncClient())

    for i, c in enumerate(clients):
        with sentry_sdk.start_transaction():
            events = capture_events()

            url = "https://httpbin.org/status/200"
            if not asyncio.iscoroutinefunction(c.get):
                response = c.get(url)
            else:
                response = asyncio.get_event_loop().run_until_complete(c.get(url))

            assert response.status_code == 200
            capture_message("Testing!")

            (event,) = events
            crumb = event["breadcrumbs"]["values"][i]
            assert crumb["type"] == "http"
            assert crumb["category"] == "httplib"
            assert crumb["data"] == {
                "url": url,
                "method": "GET",
                "status_code": 200,
                "reason": "OK",
                "extra": "foo",
            }

import asyncio

import httpx

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.httpx import HttpxIntegration


def test_crumb_capture_and_hint(sentry_init, capture_events):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[HttpxIntegration()], before_breadcrumb=before_breadcrumb)
    clients = (httpx.Client(), httpx.AsyncClient())
    for i, c in enumerate(clients):
        with start_transaction():
            events = capture_events()

            url = "https://httpbin.org/status/200"
            if not asyncio.iscoroutinefunction(c.get):
                response = c.get(url)
            else:
                response = asyncio.get_event_loop().run_until_complete(c.get(url))

            assert response.status_code == 200
            capture_message("Testing!")

            (event,) = events
            # send request twice so we need get breadcrumb by index
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


def test_outgoing_trace_headers(sentry_init):
    sentry_init(traces_sample_rate=1.0, integrations=[HttpxIntegration()])
    clients = (httpx.Client(), httpx.AsyncClient())
    for i, c in enumerate(clients):
        with start_transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            # make trace_id difference between transactions
            trace_id=f"012345678901234567890123456789{i}",
        ) as transaction:
            url = "https://httpbin.org/status/200"
            if not asyncio.iscoroutinefunction(c.get):
                response = c.get(url)
            else:
                response = asyncio.get_event_loop().run_until_complete(c.get(url))

            request_span = transaction._span_recorder.spans[-1]
            assert response.request.headers[
                "sentry-trace"
            ] == "{trace_id}-{parent_span_id}-{sampled}".format(
                trace_id=transaction.trace_id,
                parent_span_id=request_span.span_id,
                sampled=1,
            )

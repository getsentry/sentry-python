import pytest

pytest.importorskip("channels")

from channels.testing import HttpCommunicator

from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.myapp.asgi import application


@pytest.mark.asyncio
async def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/view-exc?test=query")
    response = await comm.get_response()
    assert response["status"] == 500

    event, = events

    exception, = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

    # Test that the ASGI middleware got set up correctly. Right now this needs
    # to be installed manually (see myapp/asgi.py)
    assert event["transaction"] == "/view-exc"
    assert event["request"] == {
        "cookies": {},
        "headers": {},
        "method": "GET",
        "query_string": "test=query",
        "url": "/view-exc",
    }

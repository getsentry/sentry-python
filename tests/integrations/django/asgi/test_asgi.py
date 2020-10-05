import django
import pytest
from channels.testing import HttpCommunicator
from sentry_sdk import capture_message
from sentry_sdk.integrations.django import DjangoIntegration
from tests.integrations.django.myapp.asgi import channels_application

APPS = [channels_application]
if django.VERSION >= (3, 0):
    from tests.integrations.django.myapp.asgi import asgi_application

    APPS += [asgi_application]


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
async def test_basic(sentry_init, capture_events, application):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/view-exc?test=query")
    response = await comm.get_response()
    assert response["status"] == 500

    (event,) = events

    (exception,) = event["exception"]["values"]
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

    capture_message("hi")
    event = events[-1]
    assert "request" not in event


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
async def test_async_views(sentry_init, capture_events, application):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/async_ok")
    response = await comm.get_response()
    assert response["status"] == 200

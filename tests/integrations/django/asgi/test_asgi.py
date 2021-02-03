import django
import pytest
from channels.testing import HttpCommunicator
from sentry_sdk import capture_message
from sentry_sdk.integrations.django import DjangoIntegration
from tests.integrations.django.myapp.asgi import channels_application

APPS = [channels_application]
ASGI_APP = []

if django.VERSION >= (3, 0):
    from tests.integrations.django.myapp.asgi import asgi_application

    ASGI_APP = [asgi_application]
    APPS += ASGI_APP


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
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_views(sentry_init, capture_events, application):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/async_message")
    response = await comm.get_response()
    assert response["status"] == 200

    (event,) = events

    assert event["transaction"] == "/async_message"
    assert event["request"] == {
        "cookies": {},
        "headers": {},
        "method": "GET",
        "query_string": None,
        "url": "/async_message",
    }


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_views_concurrent_execution(
    sentry_init, capture_events, application, settings
):

    import asyncio
    import time

    if application is asgi_application:
        settings.MIDDLEWARE = []
        asgi_application.load_middleware(is_async=True)

    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    comm = HttpCommunicator(application, "GET", "/my_async_view")
    comm2 = HttpCommunicator(application, "GET", "/my_async_view")
    start = time.time()

    (resp1, resp2), _ = await asyncio.wait(
        [comm.get_response(timeout=5), comm2.get_response(timeout=5)]
    )

    end = time.time()

    assert resp1.result()["status"] == 200
    assert resp2.result()["status"] == 200

    assert end - start < 1.5


@pytest.mark.parametrize("application", ASGI_APP)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_middleware_spans(
    sentry_init, render_span_tree, capture_events, application, settings
):

    settings.MIDDLEWARE = [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "tests.integrations.django.myapp.settings.TestMiddleware",
    ]
    asgi_application.load_middleware(is_async=True)

    sentry_init(
        integrations=[DjangoIntegration(middleware_spans=True)],
        traces_sample_rate=1.0,
        _experiments={"record_sql_params": True},
    )

    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/async_message")
    response = await comm.get_response()
    assert response["status"] == 200

    await comm.wait()

    message, transaction = events

    assert (
        render_span_tree(transaction)
        == """\
- op="http.server": description=null
  - op="django.middleware": description="django.contrib.sessions.middleware.SessionMiddleware.__acall__"
    - op="django.middleware": description="django.contrib.auth.middleware.AuthenticationMiddleware.__acall__"
      - op="django.middleware": description="django.middleware.csrf.CsrfViewMiddleware.__acall__"
        - op="django.middleware": description="tests.integrations.django.myapp.settings.TestMiddleware.__acall__"
          - op="django.middleware": description="django.middleware.csrf.CsrfViewMiddleware.process_view"
          - op="django.view": description="async_message\""""
    )

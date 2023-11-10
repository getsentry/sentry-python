import json

import django
import pytest
from channels.testing import HttpCommunicator
from sentry_sdk import capture_message
from sentry_sdk.integrations.django import DjangoIntegration
from tests.integrations.django.myapp.asgi import channels_application

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

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
@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_active_thread_id(sentry_init, capture_envelopes, endpoint, application):
    with mock.patch("sentry_sdk.profiler.PROFILE_MINIMUM_SAMPLES", 0):
        sentry_init(
            integrations=[DjangoIntegration()],
            traces_sample_rate=1.0,
            _experiments={"profiles_sample_rate": 1.0},
        )

        envelopes = capture_envelopes()

        comm = HttpCommunicator(application, "GET", endpoint)
        response = await comm.get_response()
        assert response["status"] == 200, response["body"]

        await comm.wait()

        data = json.loads(response["body"])

        envelopes = [envelope for envelope in envelopes]
        assert len(envelopes) == 1

        profiles = [item for item in envelopes[0].items if item.type == "profile"]
        assert len(profiles) == 1

        for profile in profiles:
            transactions = profile.payload.json["transactions"]
            assert len(transactions) == 1
            assert str(data["active"]) == transactions[0]["active_thread_id"]


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_views_concurrent_execution(sentry_init, settings):
    import asyncio
    import time

    settings.MIDDLEWARE = []
    asgi_application.load_middleware(is_async=True)

    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    comm = HttpCommunicator(asgi_application, "GET", "/my_async_view")
    comm2 = HttpCommunicator(asgi_application, "GET", "/my_async_view")

    loop = asyncio.get_event_loop()

    start = time.time()

    r1 = loop.create_task(comm.get_response(timeout=5))
    r2 = loop.create_task(comm2.get_response(timeout=5))

    (resp1, resp2), _ = await asyncio.wait({r1, r2})

    end = time.time()

    assert resp1.result()["status"] == 200
    assert resp2.result()["status"] == 200

    assert end - start < 1.5


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_middleware_that_is_function_concurrent_execution(
    sentry_init, settings
):
    import asyncio
    import time

    settings.MIDDLEWARE = [
        "tests.integrations.django.myapp.middleware.simple_middleware"
    ]
    asgi_application.load_middleware(is_async=True)

    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    comm = HttpCommunicator(asgi_application, "GET", "/my_async_view")
    comm2 = HttpCommunicator(asgi_application, "GET", "/my_async_view")

    loop = asyncio.get_event_loop()

    start = time.time()

    r1 = loop.create_task(comm.get_response(timeout=5))
    r2 = loop.create_task(comm2.get_response(timeout=5))

    (resp1, resp2), _ = await asyncio.wait({r1, r2})

    end = time.time()

    assert resp1.result()["status"] == 200
    assert resp2.result()["status"] == 200

    assert end - start < 1.5


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_middleware_spans(
    sentry_init, render_span_tree, capture_events, settings
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

    comm = HttpCommunicator(asgi_application, "GET", "/async_message")
    response = await comm.get_response()
    assert response["status"] == 200

    await comm.wait()

    message, transaction = events

    assert (
        render_span_tree(transaction)
        == """\
- op="http.server": description=null
  - op="event.django": description="django.db.reset_queries"
  - op="event.django": description="django.db.close_old_connections"
  - op="middleware.django": description="django.contrib.sessions.middleware.SessionMiddleware.__acall__"
    - op="middleware.django": description="django.contrib.auth.middleware.AuthenticationMiddleware.__acall__"
      - op="middleware.django": description="django.middleware.csrf.CsrfViewMiddleware.__acall__"
        - op="middleware.django": description="tests.integrations.django.myapp.settings.TestMiddleware.__acall__"
          - op="middleware.django": description="django.middleware.csrf.CsrfViewMiddleware.process_view"
          - op="view.render": description="async_message"
  - op="event.django": description="django.db.close_old_connections"
  - op="event.django": description="django.core.cache.close_caches"
  - op="event.django": description="django.core.handlers.base.reset_urlconf\""""
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_has_trace_if_performance_enabled(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], traces_sample_rate=1.0)

    events = capture_events()

    comm = HttpCommunicator(asgi_application, "GET", "/view-exc-with-msg")
    response = await comm.get_response()
    assert response["status"] == 500

    # ASGI Django does not create transactions per default,
    # so we do not have a transaction_event here.
    (msg_event, error_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_has_trace_if_performance_disabled(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()])

    events = capture_events()

    comm = HttpCommunicator(asgi_application, "GET", "/view-exc-with-msg")
    response = await comm.get_response()
    assert response["status"] == 500

    (msg_event, error_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]
    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_trace_from_headers_if_performance_enabled(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], traces_sample_rate=1.0)

    events = capture_events()

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    comm = HttpCommunicator(
        asgi_application,
        "GET",
        "/view-exc-with-msg",
        headers=[(b"sentry-trace", sentry_trace_header.encode())],
    )
    response = await comm.get_response()
    assert response["status"] == 500

    # ASGI Django does not create transactions per default,
    # so we do not have a transaction_event here.
    (msg_event, error_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_trace_from_headers_if_performance_disabled(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()])

    events = capture_events()

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    comm = HttpCommunicator(
        asgi_application,
        "GET",
        "/view-exc-with-msg",
        headers=[(b"sentry-trace", sentry_trace_header.encode())],
    )
    response = await comm.get_response()
    assert response["status"] == 500

    (msg_event, error_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.parametrize("application", APPS)
@pytest.mark.parametrize(
    "body,expected_return_data",
    [
        (
            b'{"username":"xyz","password":"xyz"}',
            {"username": "xyz", "password": "xyz"},
        ),
        (b"hello", ""),
        (b"", None),
    ],
)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_asgi_request_body(
    sentry_init, capture_envelopes, application, body, expected_return_data
):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    envelopes = capture_envelopes()

    comm = HttpCommunicator(
        application,
        method="POST",
        path=reverse("post_echo_async"),
        body=body,
        headers=[(b"content-type", b"application/json")],
    )
    response = await comm.get_response()

    assert response["status"] == 200
    assert response["body"] == body

    (envelope,) = envelopes
    event = envelope.get_event()

    if expected_return_data is not None:
        assert event["request"]["data"] == expected_return_data
    else:
        assert "data" not in event["request"]

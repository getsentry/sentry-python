import base64
import sys
import json
import inspect
import asyncio
import os
from unittest import mock

import django
import pytest
from channels.testing import HttpCommunicator
from sentry_sdk import capture_message
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.django.asgi import _asgi_middleware_mixin_factory
from tests.integrations.django.myapp.asgi import channels_application

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse


APPS = [channels_application]
if django.VERSION >= (3, 0):
    from tests.integrations.django.myapp.asgi import asgi_application

    APPS += [asgi_application]


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.forked
async def test_basic(sentry_init, capture_events, application):
    if sys.version_info <= (3, 8):
        with pytest.warns(DeprecationWarning):
            sentry_init(
                integrations=[DjangoIntegration()],
                send_default_pii=True,
            )
    else:
        sentry_init(
            integrations=[DjangoIntegration()],
            send_default_pii=True,
        )

    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/view-exc?test=query")
    response = await comm.get_response()
    await comm.wait()

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
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_views(sentry_init, capture_events, application):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
    )

    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/async_message")
    response = await comm.get_response()
    await comm.wait()

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
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_active_thread_id(
    sentry_init, capture_envelopes, teardown_profiling, endpoint, application
):
    with mock.patch(
        "sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0
    ):
        sentry_init(
            integrations=[DjangoIntegration()],
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
        )

        envelopes = capture_envelopes()

        comm = HttpCommunicator(application, "GET", endpoint)
        response = await comm.get_response()
        await comm.wait()

        assert response["status"] == 200, response["body"]

    assert len(envelopes) == 1

    profiles = [item for item in envelopes[0].items if item.type == "profile"]
    assert len(profiles) == 1

    data = json.loads(response["body"])

    for item in profiles:
        transactions = item.payload.json["transactions"]
        assert len(transactions) == 1
        assert str(data["active"]) == transactions[0]["active_thread_id"]

    transactions = [item for item in envelopes[0].items if item.type == "transaction"]
    assert len(transactions) == 1

    for item in transactions:
        transaction = item.payload.json
        trace_context = transaction["contexts"]["trace"]
        assert str(data["active"]) == trace_context["data"]["thread.id"]


@pytest.mark.asyncio
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_views_concurrent_execution(sentry_init, settings):
    import asyncio
    import time

    settings.MIDDLEWARE = []
    asgi_application.load_middleware(is_async=True)

    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
    )

    comm = HttpCommunicator(
        asgi_application, "GET", "/my_async_view"
    )  # sleeps for 1 second
    comm2 = HttpCommunicator(
        asgi_application, "GET", "/my_async_view"
    )  # sleeps for 1 second

    loop = asyncio.get_event_loop()

    start = time.time()

    r1 = loop.create_task(comm.get_response(timeout=5))
    r2 = loop.create_task(comm2.get_response(timeout=5))

    (resp1, resp2), _ = await asyncio.wait({r1, r2})

    end = time.time()

    assert resp1.result()["status"] == 200
    assert resp2.result()["status"] == 200

    assert (
        end - start < 2
    )  # it takes less than 2 seconds so it was ececuting concurrently


@pytest.mark.asyncio
@pytest.mark.forked
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

    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
    )

    comm = HttpCommunicator(
        asgi_application, "GET", "/my_async_view"
    )  # sleeps for 1 second
    comm2 = HttpCommunicator(
        asgi_application, "GET", "/my_async_view"
    )  # sleeps for 1 second

    loop = asyncio.get_event_loop()

    start = time.time()

    r1 = loop.create_task(comm.get_response(timeout=5))
    r2 = loop.create_task(comm2.get_response(timeout=5))

    (resp1, resp2), _ = await asyncio.wait({r1, r2})

    end = time.time()

    assert resp1.result()["status"] == 200
    assert resp2.result()["status"] == 200

    assert (
        end - start < 2
    )  # it takes less than 2 seconds so it was ececuting concurrently


@pytest.mark.asyncio
@pytest.mark.forked
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

    comm = HttpCommunicator(asgi_application, "GET", "/simple_async_view")
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200

    (transaction,) = events

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
          - op="view.render": description="simple_async_view"
  - op="event.django": description="django.db.close_old_connections"
  - op="event.django": description="django.core.cache.close_caches"
  - op="event.django": description="django.core.handlers.base.reset_urlconf\""""
    )


@pytest.mark.asyncio
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_has_trace_if_performance_enabled(sentry_init, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    comm = HttpCommunicator(asgi_application, "GET", "/view-exc-with-msg")
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 500

    (msg_event, error_event, transaction_event) = events

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
    )


@pytest.mark.asyncio
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_has_trace_if_performance_disabled(sentry_init, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
    )

    events = capture_events()

    comm = HttpCommunicator(asgi_application, "GET", "/view-exc-with-msg")
    response = await comm.get_response()
    await comm.wait()

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
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_trace_from_headers_if_performance_enabled(sentry_init, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

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
    await comm.wait()

    assert response["status"] == 500

    (msg_event, error_event, transaction_event) = events

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id
    assert transaction_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.asyncio
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_trace_from_headers_if_performance_disabled(sentry_init, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
    )

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
    await comm.wait()

    assert response["status"] == 500

    (msg_event, error_event) = events

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


PICTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image.png")
BODY_FORM = """--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="username"\r\n\r\nJane\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="password"\r\n\r\nhello123\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="photo"; filename="image.png"\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n\r\n{{image_data}}\r\n--fd721ef49ea403a6--\r\n""".replace(
    "{{image_data}}", base64.b64encode(open(PICTURE, "rb").read()).decode("utf-8")
).encode(
    "utf-8"
)
BODY_FORM_CONTENT_LENGTH = str(len(BODY_FORM)).encode("utf-8")


@pytest.mark.parametrize("application", APPS)
@pytest.mark.parametrize(
    "send_default_pii,method,headers,url_name,body,expected_data",
    [
        (
            True,
            "POST",
            [(b"content-type", b"text/plain")],
            "post_echo_async",
            b"",
            None,
        ),
        (
            True,
            "POST",
            [(b"content-type", b"text/plain")],
            "post_echo_async",
            b"some raw text body",
            "",
        ),
        (
            True,
            "POST",
            [(b"content-type", b"application/json")],
            "post_echo_async",
            b'{"username":"xyz","password":"xyz"}',
            {"username": "xyz", "password": "[Filtered]"},
        ),
        (
            True,
            "POST",
            [(b"content-type", b"application/xml")],
            "post_echo_async",
            b'<?xml version="1.0" encoding="UTF-8"?><root></root>',
            "",
        ),
        (
            True,
            "POST",
            [
                (b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"),
                (b"content-length", BODY_FORM_CONTENT_LENGTH),
            ],
            "post_echo_async",
            BODY_FORM,
            {"password": "[Filtered]", "photo": "", "username": "Jane"},
        ),
        (
            False,
            "POST",
            [(b"content-type", b"text/plain")],
            "post_echo_async",
            b"",
            None,
        ),
        (
            False,
            "POST",
            [(b"content-type", b"text/plain")],
            "post_echo_async",
            b"some raw text body",
            "",
        ),
        (
            False,
            "POST",
            [(b"content-type", b"application/json")],
            "post_echo_async",
            b'{"username":"xyz","password":"xyz"}',
            {"username": "xyz", "password": "[Filtered]"},
        ),
        (
            False,
            "POST",
            [(b"content-type", b"application/xml")],
            "post_echo_async",
            b'<?xml version="1.0" encoding="UTF-8"?><root></root>',
            "",
        ),
        (
            False,
            "POST",
            [
                (b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"),
                (b"content-length", BODY_FORM_CONTENT_LENGTH),
            ],
            "post_echo_async",
            BODY_FORM,
            {"password": "[Filtered]", "photo": "", "username": "Jane"},
        ),
    ],
)
@pytest.mark.asyncio
@pytest.mark.forked
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_asgi_request_body(
    sentry_init,
    capture_envelopes,
    application,
    send_default_pii,
    method,
    headers,
    url_name,
    body,
    expected_data,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=send_default_pii,
    )

    envelopes = capture_envelopes()

    comm = HttpCommunicator(
        application,
        method=method,
        headers=headers,
        path=reverse(url_name),
        body=body,
    )
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200
    assert response["body"] == body

    (envelope,) = envelopes
    event = envelope.get_event()

    if expected_data is not None:
        assert event["request"]["data"] == expected_data
    else:
        assert "data" not in event["request"]


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.version_info >= (3, 12),
    reason=(
        "asyncio.iscoroutinefunction has been replaced in 3.12 by inspect.iscoroutinefunction"
    ),
)
async def test_asgi_mixin_iscoroutinefunction_before_3_12():
    sentry_asgi_mixin = _asgi_middleware_mixin_factory(lambda: None)

    async def get_response(): ...

    instance = sentry_asgi_mixin(get_response)
    assert asyncio.iscoroutinefunction(instance)


@pytest.mark.skipif(
    sys.version_info >= (3, 12),
    reason=(
        "asyncio.iscoroutinefunction has been replaced in 3.12 by inspect.iscoroutinefunction"
    ),
)
def test_asgi_mixin_iscoroutinefunction_when_not_async_before_3_12():
    sentry_asgi_mixin = _asgi_middleware_mixin_factory(lambda: None)

    def get_response(): ...

    instance = sentry_asgi_mixin(get_response)
    assert not asyncio.iscoroutinefunction(instance)


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason=(
        "asyncio.iscoroutinefunction has been replaced in 3.12 by inspect.iscoroutinefunction"
    ),
)
async def test_asgi_mixin_iscoroutinefunction_after_3_12():
    sentry_asgi_mixin = _asgi_middleware_mixin_factory(lambda: None)

    async def get_response(): ...

    instance = sentry_asgi_mixin(get_response)
    assert inspect.iscoroutinefunction(instance)


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason=(
        "asyncio.iscoroutinefunction has been replaced in 3.12 by inspect.iscoroutinefunction"
    ),
)
def test_asgi_mixin_iscoroutinefunction_when_not_async_after_3_12():
    sentry_asgi_mixin = _asgi_middleware_mixin_factory(lambda: None)

    def get_response(): ...

    instance = sentry_asgi_mixin(get_response)
    assert not inspect.iscoroutinefunction(instance)


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
async def test_async_view(sentry_init, capture_events, application):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    (event,) = events
    assert event["type"] == "transaction"
    assert event["transaction"] == "/simple_async_view"


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
async def test_transaction_http_method_default(
    sentry_init, capture_events, application
):
    """
    By default OPTIONS and HEAD requests do not create a transaction.
    """
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "OPTIONS", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "HEAD", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    (event,) = events

    assert len(events) == 1
    assert event["request"]["method"] == "GET"


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
async def test_transaction_http_method_custom(sentry_init, capture_events, application):
    sentry_init(
        integrations=[
            DjangoIntegration(
                http_methods_to_capture=(
                    "OPTIONS",
                    "head",
                ),  # capitalization does not matter
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "OPTIONS", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "HEAD", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    assert len(events) == 2

    (event1, event2) = events
    assert event1["request"]["method"] == "OPTIONS"
    assert event2["request"]["method"] == "HEAD"

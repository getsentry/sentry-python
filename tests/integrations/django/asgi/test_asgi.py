import asyncio
import base64
import inspect
import json
import os
import sys

import django
import pytest
from channels.testing import HttpCommunicator

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.django.asgi import _asgi_middleware_mixin_factory
from tests.integrations.django.myapp.asgi import channels_application
from tests.integrations.django.utils import pytest_mark_django_db_decorator
from tests.integrations.utils import DATA_COLLECTION_USER_INFO_CASES

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse


APPS = [channels_application]
if django.VERSION >= (3, 0):
    from tests.integrations.django.myapp.asgi import asgi_application

    APPS += [asgi_application]


@pytest.fixture
def make_asgi_application():
    """Build a fresh ASGI application. Call AFTER sentry_init so middleware
    instrumentation is installed before Django builds the middleware chain."""
    from django.core.asgi import get_asgi_application

    return get_asgi_application


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 0), reason="Django ASGI support shipped in 3.0"
)
async def test_basic(
    sentry_init,
    capture_items,
    application,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    import channels  # type: ignore[import-not-found]

    items = capture_items("event")

    if (
        sys.version_info < (3, 9)
        and channels.__version__ < "4.0.0"
        and django.VERSION >= (3, 0)
        and django.VERSION < (4, 0)
    ):
        # We emit a UserWarning for channels 2.x and 3.x on Python 3.8 and older
        # because the async support was not really good back then and there is a known issue.
        # See the TreadingIntegration for details.
        with pytest.warns(UserWarning):
            comm = HttpCommunicator(application, "GET", "/view-exc?test=query")
            response = await comm.get_response()
            await comm.wait()
    else:
        comm = HttpCommunicator(application, "GET", "/view-exc?test=query")
        response = await comm.get_response()
        await comm.wait()

    assert response["status"] == 500

    (event,) = (item.payload for item in items)

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
    event = items[-1].payload

    assert "request" not in event


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_views(
    sentry_init,
    capture_items,
    application,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    comm = HttpCommunicator(application, "GET", "/async_message")
    items = capture_items("event")

    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200

    (event,) = (item.payload for item in items)

    assert event["transaction"] == "/async_message"
    assert event["request"] == {
        "cookies": {},
        "headers": {},
        "method": "GET",
        "query_string": None,
        "url": "/async_message",
    }


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_views_concurrent_execution(
    sentry_init, settings, make_asgi_application
):
    import time

    settings.MIDDLEWARE = []
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
    )

    application = make_asgi_application()

    comm = HttpCommunicator(application, "GET", "/my_async_view")  # sleeps for 1 second
    comm2 = HttpCommunicator(
        application, "GET", "/my_async_view"
    )  # sleeps for 1 second

    start = time.monotonic()
    resp1, resp2 = await asyncio.gather(
        comm.get_response(timeout=5),
        comm2.get_response(timeout=5),
    )
    await asyncio.gather(comm.wait(), comm2.wait())
    end = time.monotonic()

    assert resp1["status"] == 200
    assert resp2["status"] == 200

    assert (
        end - start < 2
    )  # it takes less than 2 seconds so it was ececuting concurrently


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_middleware_that_is_function_concurrent_execution(
    sentry_init, settings, make_asgi_application
):
    import time

    settings.MIDDLEWARE = [
        "tests.integrations.django.myapp.middleware.simple_middleware"
    ]
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
    )

    application = make_asgi_application()

    comm = HttpCommunicator(application, "GET", "/my_async_view")  # sleeps for 1 second
    comm2 = HttpCommunicator(
        application, "GET", "/my_async_view"
    )  # sleeps for 1 second

    start = time.monotonic()
    resp1, resp2 = await asyncio.gather(
        comm.get_response(timeout=5),
        comm2.get_response(timeout=5),
    )
    await asyncio.gather(comm.wait(), comm2.wait())
    end = time.monotonic()

    assert resp1["status"] == 200
    assert resp2["status"] == 200

    assert (
        end - start < 2
    )  # it takes less than 2 seconds so it was ececuting concurrently


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_middleware_spans(
    sentry_init,
    render_span_tree,
    capture_items,
    settings,
    make_asgi_application,
):
    settings.MIDDLEWARE = [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "tests.integrations.django.myapp.settings.TestMiddleware",
    ]
    sentry_init(
        integrations=[DjangoIntegration(middleware_spans=True)],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        _experiments={
            "record_sql_params": True,
        },
    )

    application = make_asgi_application()

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    items = capture_items("span")

    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    # Filter out signal-receiver spans — their ordering depends on Django
    # module import order and is not what this middleware test verifies.
    spans = [s for s in spans if s["attributes"].get("sentry.op") != "event.django"]

    assert (
        render_span_tree(spans)
        == """\
- sentry.op="http.server": name="/simple_async_view"
  - sentry.op="middleware.django": name="django.contrib.sessions.middleware.SessionMiddleware.__acall__"
    - sentry.op="middleware.django": name="django.contrib.auth.middleware.AuthenticationMiddleware.__acall__"
      - sentry.op="middleware.django": name="django.middleware.csrf.CsrfViewMiddleware.__acall__"
        - sentry.op="middleware.django": name="tests.integrations.django.myapp.settings.TestMiddleware.__acall__"
          - sentry.op="middleware.django": name="django.middleware.csrf.CsrfViewMiddleware.process_view"
          - sentry.op="view.render": name="simple_async_view\""""
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_has_trace_if_performance_enabled(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    comm = HttpCommunicator(asgi_application, "GET", "/view-exc-with-msg")
    items = capture_items("event", "span")

    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 500

    (
        msg_event,
        error_event,
    ) = (item.payload for item in items if item.type == "event")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[6]["is_segment"] is True

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == spans[6]["trace_id"]
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_has_trace_if_performance_disabled(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        trace_lifecycle="stream",
    )

    comm = HttpCommunicator(asgi_application, "GET", "/view-exc-with-msg")
    items = capture_items("event")

    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 500

    (
        msg_event,
        error_event,
    ) = (item.payload for item in items)

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
async def test_trace_from_headers_if_performance_enabled(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    comm = HttpCommunicator(
        asgi_application,
        "GET",
        "/view-exc-with-msg",
        headers=[(b"sentry-trace", sentry_trace_header.encode())],
    )

    items = capture_items("event", "span")

    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 500

    (
        msg_event,
        error_event,
    ) = (item.payload for item in items if item.type == "event")

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[6]["is_segment"] is True
    assert spans[6]["trace_id"] == trace_id


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_trace_from_headers_if_performance_disabled(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        trace_lifecycle="stream",
    )

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    comm = HttpCommunicator(
        asgi_application,
        "GET",
        "/view-exc-with-msg",
        headers=[(b"sentry-trace", sentry_trace_header.encode())],
    )

    items = capture_items("event")

    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 500

    (msg_event, error_event) = (item.payload for item in items)

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


PICTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image.png")
BODY_FORM = """--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="username"\r\n\r\nJane\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="password"\r\n\r\nhello123\r\n--fd721ef49ea403a6\r\nContent-Disposition: form-data; name="photo"; filename="image.png"\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n\r\n{{image_data}}\r\n--fd721ef49ea403a6--\r\n""".replace(
    "{{image_data}}", base64.b64encode(open(PICTURE, "rb").read()).decode("utf-8")
).encode("utf-8")
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
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_asgi_request_body(
    sentry_init,
    capture_items,
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
        trace_lifecycle="stream",
    )

    comm = HttpCommunicator(
        application,
        method=method,
        headers=headers,
        path=reverse(url_name),
        body=body,
    )

    items = capture_items("event")

    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200
    assert response["body"] == body

    sentry_sdk.flush()
    (event,) = (item.payload for item in items)

    if expected_data is not None:
        assert event["request"]["data"] == expected_data
    else:
        assert "data" not in event["request"]


@pytest.mark.parametrize("application", APPS)
@pytest.mark.parametrize(
    "data_collection, expect_body",
    [
        pytest.param({}, True, id="data_collection_http_bodies_default"),
        pytest.param(
            {"http_bodies": ["incoming_request"]},
            True,
            id="data_collection_http_bodies_incoming_request",
        ),
        pytest.param(
            {"http_bodies": ["outgoing_request"]},
            False,
            id="data_collection_http_bodies_outgoing_request_only",
        ),
        pytest.param(
            {"http_bodies": []}, False, id="data_collection_http_bodies_empty"
        ),
    ],
)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_asgi_request_body_data_collection(
    sentry_init, capture_events, application, data_collection, expect_body
):
    sentry_init(
        integrations=[DjangoIntegration()],
        _experiments={"data_collection": data_collection},
    )
    events = capture_events()

    data = {"hey": 42}
    comm = HttpCommunicator(
        application,
        method="POST",
        headers=[(b"content-type", b"application/json")],
        path=reverse("post_echo_async"),
        body=json.dumps(data).encode("utf-8"),
    )
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200

    (event,) = events

    if expect_body:
        assert event["request"]["data"] == data
    else:
        assert "data" not in event["request"]


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_asgi_request_body_dropped_with_form_and_files_data_collection(
    sentry_init, capture_events, application
):
    sentry_init(
        integrations=[DjangoIntegration()],
        max_request_body_size="always",
        _experiments={"data_collection": {"http_bodies": []}},
    )
    events = capture_events()

    comm = HttpCommunicator(
        application,
        method="POST",
        headers=[
            (b"content-type", b"multipart/form-data; boundary=fd721ef49ea403a6"),
            (b"content-length", BODY_FORM_CONTENT_LENGTH),
        ],
        path=reverse("post_echo_async"),
        body=BODY_FORM,
    )
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200

    (event,) = events

    assert "data" not in event["request"]
    assert "data" not in event.get("_meta", {}).get("request", {})


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_asgi_transaction_request_body_data_collection(
    sentry_init, capture_events, application
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={"data_collection": {"http_bodies": []}},
    )
    events = capture_events()

    comm = HttpCommunicator(
        application,
        method="POST",
        headers=[(b"content-type", b"application/json")],
        path=reverse("post_echo_async"),
        body=json.dumps({"hey": 42}).encode("utf-8"),
    )
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200

    (event, transaction_event) = events

    assert "data" not in event["request"]
    assert "data" not in transaction_event["request"]


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_asgi_oversized_request_body_not_annotated_data_collection(
    sentry_init, capture_events, application
):
    """
    The gating happens before the size check, so an oversized body is dropped
    outright instead of being reported as removed because of the size limit.
    """
    sentry_init(
        integrations=[DjangoIntegration()],
        max_request_body_size="small",
        _experiments={"data_collection": {"http_bodies": []}},
    )
    events = capture_events()

    comm = HttpCommunicator(
        application,
        method="POST",
        headers=[(b"content-type", b"text/plain")],
        path=reverse("post_echo_async"),
        body=b"a" * 2000,
    )
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200

    (event,) = events

    assert "data" not in event["request"]
    assert "data" not in event.get("_meta", {}).get("request", {})


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.version_info >= (3, 12),
    reason=(
        "asyncio.iscoroutinefunction has been replaced in 3.12 by inspect.iscoroutinefunction"
    ),
)
@pytest.mark.skipif(
    django.VERSION < (3, 0), reason="Django ASGI support shipped in 3.0"
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
@pytest.mark.skipif(
    django.VERSION < (3, 1), reason="async views have been introduced in Django 3.1"
)
async def test_async_view(
    sentry_init,
    capture_items,
    application,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    items = capture_items("span")

    await comm.get_response()
    await comm.wait()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert spans[5]["name"] == "/simple_async_view"


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 0), reason="Django ASGI support shipped in 3.0"
)
async def test_transaction_http_method_default(
    sentry_init,
    capture_items,
    application,
):
    """
    By default OPTIONS and HEAD requests do not create a transaction.
    """
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "OPTIONS", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "HEAD", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert spans[5]["attributes"]["http.request.method"] == "GET"


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 0), reason="Django ASGI support shipped in 3.0"
)
async def test_transaction_http_method_custom(
    sentry_init,
    capture_items,
    application,
):
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "OPTIONS", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    comm = HttpCommunicator(application, "HEAD", "/simple_async_view")
    await comm.get_response()
    await comm.wait()

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    assert spans[5]["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "OPTIONS"
    assert spans[11]["attributes"][SPANDATA.HTTP_REQUEST_METHOD] == "HEAD"


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1),
    reason="async views/middleware introduced in Django 3.1",
)
async def test_async_middleware_process_view_is_awaited(
    sentry_init, settings, make_asgi_application
):
    """Regression test for async ``process_view`` being coerced to sync."""
    sentry_init(integrations=[DjangoIntegration()])

    settings.MIDDLEWARE = [
        "tests.integrations.django.myapp.middleware.AsyncProcessViewMiddleware"
    ]
    application = make_asgi_application()

    comm = HttpCommunicator(application, "GET", "/simple_async_view")
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200


@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 1),
    reason="async views/middleware introduced in Django 3.1",
)
async def test_async_middleware_process_exception_is_awaited(
    sentry_init, settings, make_asgi_application
):
    """Regression test for async ``process_exception`` being coerced to sync."""
    sentry_init(integrations=[DjangoIntegration()])

    settings.MIDDLEWARE = [
        "tests.integrations.django.myapp.middleware.AsyncProcessExceptionMiddleware"
    ]
    application = make_asgi_application()

    comm = HttpCommunicator(application, "GET", "/view-exc")
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 200
    assert response["body"] == b"handled by async process_exception"


@pytest.mark.forked
@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 0), reason="Django ASGI support shipped in 3.0"
)
@pytest.mark.parametrize("init_kwargs, expect_user", DATA_COLLECTION_USER_INFO_CASES)
@pytest_mark_django_db_decorator()
async def test_user_identity_error_event_data_collection(
    sentry_init, capture_events, application, init_kwargs, expect_user
):
    sentry_init(integrations=[DjangoIntegration()], **init_kwargs)
    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/mylogin-with-exception")
    await comm.get_response()
    await comm.wait()

    event = events[-1]

    if expect_user:
        assert event["user"]["id"] == "1"
        assert event["user"]["email"] == "lennon@thebeatles.com"
        assert event["user"]["username"] == "john"
    else:
        assert "id" not in event.get("user", {})
        assert "email" not in event.get("user", {})
        assert "username" not in event.get("user", {})


@pytest.mark.parametrize("application", APPS)
@pytest.mark.asyncio
@pytest.mark.skipif(
    django.VERSION < (3, 0), reason="Django ASGI support shipped in 3.0"
)
@pytest.mark.parametrize(
    ("integration_kwargs", "expected_type"),
    (
        ({}, None),
        ({"failed_request_status_codes": {403, *range(500, 600)}}, "PermissionDenied"),
    ),
)
async def test_failed_request_status_codes(
    sentry_init, capture_events, application, integration_kwargs, expected_type
):
    sentry_init(integrations=[DjangoIntegration(**integration_kwargs)])
    events = capture_events()

    comm = HttpCommunicator(application, "GET", "/permission-denied-exc")
    response = await comm.get_response()
    await comm.wait()

    assert response["status"] == 403

    if expected_type is None:
        assert not events
    else:
        (event,) = events
        (exception,) = event["exception"]["values"]
        assert exception["type"] == expected_type
        assert exception["mechanism"]["handled"] is True
        assert event["transaction"] == "/permission-denied-exc"

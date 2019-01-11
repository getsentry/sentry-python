from __future__ import absolute_import

import platform
import threading

import pytest

from werkzeug.test import Client
from django.contrib.auth.models import User
from django.core.management import execute_from_command_line
from django.db.utils import OperationalError


try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk import capture_message, capture_exception
from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.myapp.wsgi import application


@pytest.fixture(autouse=True)
def django_clear_caches():
    """Invalidate the connection caches.

    https://github.com/pytest-dev/pytest-django/issues/587
    """
    from django.db import connections

    connections._connections = threading.local()
    # this will clear the cached property
    connections.__dict__.pop("databases", None)


@pytest.fixture
def client():
    return Client(application)


def test_view_exceptions(sentry_init, client, capture_exceptions, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    exceptions = capture_exceptions()
    events = capture_events()
    client.get(reverse("view_exc"))

    error, = exceptions
    assert isinstance(error, ZeroDivisionError)

    event, = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "django"


def test_middleware_exceptions(sentry_init, client, capture_exceptions):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    exceptions = capture_exceptions()
    client.get(reverse("middleware_exc"))

    error, = exceptions
    assert isinstance(error, ZeroDivisionError)


def test_request_captured(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    content, status, headers = client.get(reverse("message"))
    assert b"".join(content) == b"ok"

    event, = events
    assert event["transaction"] == "/message"
    assert event["request"] == {
        "cookies": {},
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Content-Length": "0", "Content-Type": "", "Host": "localhost"},
        "method": "GET",
        "query_string": "",
        "url": "http://localhost/message",
    }


def test_transaction_with_class_view(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(transaction_style="function_name")],
        send_default_pii=True,
    )
    events = capture_events()
    content, status, headers = client.head(reverse("classbased"))
    assert status.lower() == "200 ok"

    event, = events

    assert (
        event["transaction"] == "tests.integrations.django.myapp.views.ClassBasedView"
    )
    assert event["message"] == "hi"


@pytest.mark.django_db(transaction=True)
def test_user_captured(sentry_init, client, capture_events):

    # Honestly no idea why pytest-django does not do this
    from django import VERSION
    from django.core import management

    if VERSION < (2, 0):
        management.call_command("migrate", noinput=True)
    else:
        management.call_command("migrate", no_input=True)

    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    content, status, headers = client.get(reverse("mylogin"))
    assert b"".join(content) == b"ok"

    assert not events

    content, status, headers = client.get(reverse("message"))
    assert b"".join(content) == b"ok"

    event, = events

    assert event["user"] == {
        "email": "lennon@thebeatles.com",
        "username": "john",
        "id": "1",
    }


@pytest.mark.django_db
def test_queryset_repr(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    User.objects.create_user("john", "lennon@thebeatles.com", "johnpassword")

    try:
        my_queryset = User.objects.all()  # noqa
        1 / 0
    except Exception:
        capture_exception()

    event, = events

    exception, = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    frame, = exception["stacktrace"]["frames"]
    assert frame["vars"]["my_queryset"].startswith(
        "<QuerySet from django.db.models.query at"
    )


def test_custom_error_handler_request_context(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    content, status, headers = client.post("/404")
    assert status.lower() == "404 not found"

    event, = events

    assert event["message"] == "not found"
    assert event["level"] == "error"
    assert event["request"] == {
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Content-Length": "0", "Content-Type": "", "Host": "localhost"},
        "method": "POST",
        "query_string": "",
        "url": "http://localhost/404",
    }


def test_500(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()

    content, status, headers = client.get("/view-exc")
    assert status.lower() == "500 internal server error"
    content = b"".join(content).decode("utf-8")

    event, = events
    event_id = event["event_id"]
    assert content == "Sentry error: %s" % event_id


def test_management_command_raises():
    # This just checks for our assumption that Django passes through all
    # exceptions by default, so our excepthook can be used for management
    # commands.
    with pytest.raises(ZeroDivisionError):
        execute_from_command_line(["manage.py", "mycrash"])


@pytest.mark.django_db(transaction=True)
def test_sql_queries(request, sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    from django.db import connection

    events = capture_events()

    with connection.cursor() as sql:
        with pytest.raises(OperationalError):
            # table doesn't even exist
            sql.execute("""SELECT count(*) FROM people_person WHERE foo = %s""", [123])

    capture_message("HI")

    event, = events

    crumb, = event["breadcrumbs"]

    assert crumb["message"] == """SELECT count(*) FROM people_person WHERE foo = 123"""


@pytest.mark.django_db(transaction=True)
def test_sql_dict_query_params(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    from django.db import connection

    sql = connection.cursor()

    events = capture_events()
    with pytest.raises(OperationalError):
        # This really only works with postgres. sqlite will crash with syntax error
        sql.execute(
            """SELECT count(*) FROM people_person WHERE foo = %(my_foo)s""",
            {"my_foo": 10},
        )

    capture_message("HI")

    event, = events

    crumb, = event["breadcrumbs"]
    assert crumb["message"] == ("SELECT count(*) FROM people_person WHERE foo = 10")


@pytest.mark.skipif(
    platform.python_implementation() == "PyPy", reason="psycopg broken on pypy"
)
@pytest.mark.django_db(transaction=True)
def test_sql_psycopg2_string_composition(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    from django.db import connection
    from psycopg2 import sql as psycopg2_sql

    sql = connection.cursor()

    events = capture_events()
    with pytest.raises(TypeError):
        # crashes because we use sqlite
        sql.execute(
            psycopg2_sql.SQL("SELECT %(my_param)s FROM people_person"), {"my_param": 10}
        )

    capture_message("HI")

    event, = events

    crumb, = event["breadcrumbs"]
    assert crumb["message"] == ("SELECT 10 FROM people_person")


@pytest.mark.django_db(transaction=True)
def test_sql_queries_large_params(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    from django.db import connection

    sql = connection.cursor()

    events = capture_events()
    with pytest.raises(OperationalError):
        # table doesn't even exist
        sql.execute(
            """SELECT count(*) FROM people_person WHERE foo = %s""", ["x" * 1000]
        )

    capture_message("HI")

    event, = events

    crumb, = event["breadcrumbs"]
    assert crumb["message"] == (
        "SELECT count(*) FROM people_person WHERE foo = '%s..." % ("x" * 508,)
    )


@pytest.mark.parametrize(
    "transaction_style,expected_transaction",
    [
        ("function_name", "tests.integrations.django.myapp.views.message"),
        ("url", "/message"),
    ],
)
def test_transaction_style(
    sentry_init, client, capture_events, transaction_style, expected_transaction
):
    sentry_init(
        integrations=[DjangoIntegration(transaction_style=transaction_style)],
        send_default_pii=True,
    )
    events = capture_events()
    content, status, headers = client.get(reverse("message"))
    assert b"".join(content) == b"ok"

    event, = events
    assert event["transaction"] == expected_transaction


def test_request_body(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    content, status, headers = client.post(
        reverse("post_echo"), data=b"heyooo", content_type="text/plain"
    )
    assert status.lower() == "200 ok"
    assert b"".join(content) == b"heyooo"

    event, = events

    assert event["message"] == "hi"
    assert event["request"]["data"] == ""
    assert event["_meta"]["request"]["data"][""] == {
        "len": 6,
        "rem": [["!raw", "x", 0, 6]],
    }

    del events[:]

    content, status, headers = client.post(
        reverse("post_echo"), data=b'{"hey": 42}', content_type="application/json"
    )
    assert status.lower() == "200 ok"
    assert b"".join(content) == b'{"hey": 42}'

    event, = events

    assert event["message"] == "hi"
    assert event["request"]["data"] == {"hey": 42}
    assert "" not in event


def test_template_exception(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()

    content, status, headers = client.get(reverse("template_exc"))
    assert status.lower() == "500 internal server error"

    event, = events
    exception = event["exception"]["values"][0]

    frames = [
        f
        for f in exception["stacktrace"]["frames"]
        if not f["filename"].startswith("django/")
    ]
    view_frame, template_frame = frames[-2:]

    assert template_frame["context_line"] == "{% invalid template tag %}\n"
    assert template_frame["pre_context"] == ["5\n", "6\n", "7\n", "8\n", "9\n"]

    assert template_frame["post_context"] == ["11\n", "12\n", "13\n", "14\n", "15\n"]
    assert template_frame["lineno"] == 10
    assert template_frame["in_app"]
    assert template_frame["filename"].endswith("error.html")

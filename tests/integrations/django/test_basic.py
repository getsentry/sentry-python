from __future__ import absolute_import

import pytest
import json

from werkzeug.test import Client
from django import VERSION as DJANGO_VERSION
from django.contrib.auth.models import User
from django.core.management import execute_from_command_line
from django.db.utils import OperationalError, ProgrammingError, DataError


try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk import capture_message, capture_exception
from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.myapp.wsgi import application


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
        "headers": {"Host": "localhost"},
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


@pytest.mark.forked
@pytest.mark.django_db
def test_user_captured(sentry_init, client, capture_events):
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


@pytest.mark.forked
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
        "headers": {"Host": "localhost"},
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


@pytest.mark.forked
def test_management_command_raises():
    # This just checks for our assumption that Django passes through all
    # exceptions by default, so our excepthook can be used for management
    # commands.
    with pytest.raises(ZeroDivisionError):
        execute_from_command_line(["manage.py", "mycrash"])


@pytest.mark.forked
@pytest.mark.django_db
@pytest.mark.parametrize("with_integration", [True, False])
def test_sql_queries(sentry_init, capture_events, with_integration):
    sentry_init(
        integrations=[DjangoIntegration()] if with_integration else [],
        send_default_pii=True,
        _experiments={"record_sql_params": True},
    )

    from django.db import connection

    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        _experiments={"record_sql_params": True},
    )

    events = capture_events()

    sql = connection.cursor()

    with pytest.raises(OperationalError):
        # table doesn't even exist
        sql.execute("""SELECT count(*) FROM people_person WHERE foo = %s""", [123])

    capture_message("HI")

    event, = events

    if with_integration:
        crumb = event["breadcrumbs"][-1]

        assert crumb["message"] == "SELECT count(*) FROM people_person WHERE foo = %s"
        assert crumb["data"]["db.params"] == [123]


@pytest.mark.forked
@pytest.mark.django_db
def test_sql_dict_query_params(sentry_init, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        _experiments={"record_sql_params": True},
    )

    from django.db import connections

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    sql = connections["postgres"].cursor()

    events = capture_events()
    with pytest.raises(ProgrammingError):
        sql.execute(
            """SELECT count(*) FROM people_person WHERE foo = %(my_foo)s""",
            {"my_foo": 10},
        )

    capture_message("HI")
    event, = events

    crumb = event["breadcrumbs"][-1]
    assert crumb["message"] == (
        "SELECT count(*) FROM people_person WHERE foo = %(my_foo)s"
    )
    assert crumb["data"]["db.params"] == {"my_foo": 10}


@pytest.mark.parametrize(
    "query",
    [
        lambda sql: sql.SQL("SELECT %(my_param)s FROM {mytable}").format(
            mytable=sql.Identifier("foobar")
        ),
        lambda sql: sql.SQL('SELECT %(my_param)s FROM "foobar"'),
    ],
)
@pytest.mark.forked
@pytest.mark.django_db
def test_sql_psycopg2_string_composition(sentry_init, capture_events, query):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        _experiments={"record_sql_params": True},
    )
    from django.db import connections

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    import psycopg2.sql

    sql = connections["postgres"].cursor()

    events = capture_events()
    with pytest.raises(ProgrammingError):
        sql.execute(query(psycopg2.sql), {"my_param": 10})

    capture_message("HI")

    event, = events
    crumb = event["breadcrumbs"][-1]
    assert crumb["message"] == ('SELECT %(my_param)s FROM "foobar"')
    assert crumb["data"]["db.params"] == {"my_param": 10}


@pytest.mark.forked
@pytest.mark.django_db
def test_sql_psycopg2_placeholders(sentry_init, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        _experiments={"record_sql_params": True},
    )
    from django.db import connections

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    import psycopg2.sql

    sql = connections["postgres"].cursor()

    events = capture_events()
    with pytest.raises(DataError):
        names = ["foo", "bar"]
        identifiers = [psycopg2.sql.Identifier(name) for name in names]
        placeholders = [
            psycopg2.sql.Placeholder(var) for var in ["first_var", "second_var"]
        ]
        sql.execute("create table my_test_table (foo text, bar date)")

        query = psycopg2.sql.SQL("insert into my_test_table ({}) values ({})").format(
            psycopg2.sql.SQL(", ").join(identifiers),
            psycopg2.sql.SQL(", ").join(placeholders),
        )
        sql.execute(query, {"first_var": "fizz", "second_var": "not a date"})

    capture_message("HI")

    event, = events
    for crumb in event["breadcrumbs"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"][-2:] == [
        {
            "category": "query",
            "data": {"db.paramstyle": "format"},
            "message": "create table my_test_table (foo text, bar date)",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.params": {"first_var": "fizz", "second_var": "not a date"},
                "db.paramstyle": "format",
            },
            "message": 'insert into my_test_table ("foo", "bar") values (%(first_var)s, '
            "%(second_var)s)",
            "type": "default",
        },
    ]


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


def test_request_body(sentry_init, client, capture_events, fast_serialize):
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
    if not fast_serialize:
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


@pytest.mark.xfail
def test_read_request(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()

    content, status, headers = client.post(
        reverse("read_body_and_view_exc"),
        data=b'{"hey": 42}',
        content_type="application/json",
    )

    assert status.lower() == "500 internal server error"

    event, = events

    assert "data" not in event["request"]


def test_template_exception(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()

    content, status, headers = client.get(reverse("template_exc"))
    assert status.lower() == "500 internal server error"

    event, = events
    exception = event["exception"]["values"][-1]
    assert exception["type"] == "TemplateSyntaxError"

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

    filenames = [
        (f.get("function"), f.get("module")) for f in exception["stacktrace"]["frames"]
    ]
    assert filenames[-3:] == [
        (u"parse", u"django.template.base"),
        (None, None),
        (u"invalid_block_tag", u"django.template.base"),
    ]


@pytest.mark.parametrize(
    "route", ["rest_framework_exc", "rest_framework_read_body_and_exc"]
)
@pytest.mark.parametrize(
    "ct,body",
    [
        ["application/json", {"foo": "bar"}],
        ["application/json", 1],
        ["application/json", "foo"],
        ["application/x-www-form-urlencoded", {"foo": "bar"}],
    ],
)
def test_rest_framework_basic(
    sentry_init, client, capture_events, capture_exceptions, ct, body, route
):
    pytest.importorskip("rest_framework")
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    exceptions = capture_exceptions()
    events = capture_events()

    if ct == "application/json":
        client.post(
            reverse(route), data=json.dumps(body), content_type="application/json"
        )
    elif ct == "application/x-www-form-urlencoded":
        client.post(reverse(route), data=body)
    else:
        assert False

    error, = exceptions
    assert isinstance(error, ZeroDivisionError)

    event, = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "django"

    assert event["request"]["data"] == body
    assert event["request"]["headers"]["Content-Type"] == ct


@pytest.mark.parametrize(
    "endpoint", ["rest_permission_denied_exc", "permission_denied_exc"]
)
def test_does_not_capture_403(sentry_init, client, capture_events, endpoint):
    if endpoint == "rest_permission_denied_exc":
        pytest.importorskip("rest_framework")

    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()

    _content, status, _headers = client.get(reverse(endpoint))
    assert status.lower() == "403 forbidden"

    assert not events


def test_middleware_spans(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    _content, status, _headers = client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"

    for middleware in transaction["spans"]:
        assert middleware["op"] == "django.middleware"

    if DJANGO_VERSION >= (1, 10):
        reference_value = [
            "tests.integrations.django.myapp.settings.TestFunctionMiddleware.__call__",
            "tests.integrations.django.myapp.settings.TestMiddleware.__call__",
            "django.contrib.auth.middleware.AuthenticationMiddleware.__call__",
            "django.contrib.sessions.middleware.SessionMiddleware.__call__",
        ]
    else:
        reference_value = [
            "django.contrib.sessions.middleware.SessionMiddleware.process_request",
            "django.contrib.auth.middleware.AuthenticationMiddleware.process_request",
            "tests.integrations.django.myapp.settings.TestMiddleware.process_request",
            "tests.integrations.django.myapp.settings.TestMiddleware.process_response",
            "django.contrib.sessions.middleware.SessionMiddleware.process_response",
        ]

    assert [t["description"] for t in transaction["spans"]] == reference_value


def test_middleware_spans_disabled(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(middleware_spans=False)], traces_sample_rate=1.0
    )
    events = capture_events()

    _content, status, _headers = client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"

    assert not transaction["spans"]

from __future__ import absolute_import

import json
import pytest
import pytest_django
from functools import partial

from werkzeug.test import Client
from django import VERSION as DJANGO_VERSION
from django.contrib.auth.models import User
from django.core.management import execute_from_command_line
from django.db.utils import OperationalError, ProgrammingError, DataError

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk._compat import PY2
from sentry_sdk import capture_message, capture_exception, configure_scope
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.event.django_handlers import _get_receiver_name
from sentry_sdk.integrations.executing import ExecutingIntegration

from tests.integrations.django.myapp.wsgi import application

# Hack to prevent from experimental feature introduced in version `4.3.0` in `pytest-django` that
# requires explicit database allow from failing the test
pytest_mark_django_db_decorator = partial(pytest.mark.django_db)
try:
    pytest_version = tuple(map(int, pytest_django.__version__.split(".")))
    if pytest_version > (4, 2, 0):
        pytest_mark_django_db_decorator = partial(
            pytest.mark.django_db, databases="__all__"
        )
except ValueError:
    if "dev" in pytest_django.__version__:
        pytest_mark_django_db_decorator = partial(
            pytest.mark.django_db, databases="__all__"
        )
except AttributeError:
    pass


@pytest.fixture
def client():
    return Client(application)


def test_view_exceptions(sentry_init, client, capture_exceptions, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    exceptions = capture_exceptions()
    events = capture_events()
    client.get(reverse("view_exc"))

    (error,) = exceptions
    assert isinstance(error, ZeroDivisionError)

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "django"


def test_ensures_x_forwarded_header_is_honored_in_sdk_when_enabled_in_django(
    sentry_init, client, capture_exceptions, capture_events, settings
):
    """
    Test that ensures if django settings.USE_X_FORWARDED_HOST is set to True
    then the SDK sets the request url to the `HTTP_X_FORWARDED_FOR`
    """
    settings.USE_X_FORWARDED_HOST = True

    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    exceptions = capture_exceptions()
    events = capture_events()
    client.get(reverse("view_exc"), headers={"X_FORWARDED_HOST": "example.com"})

    (error,) = exceptions
    assert isinstance(error, ZeroDivisionError)

    (event,) = events
    assert event["request"]["url"] == "http://example.com/view-exc"


def test_ensures_x_forwarded_header_is_not_honored_when_unenabled_in_django(
    sentry_init, client, capture_exceptions, capture_events
):
    """
    Test that ensures if django settings.USE_X_FORWARDED_HOST is set to False
    then the SDK sets the request url to the `HTTP_POST`
    """
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    exceptions = capture_exceptions()
    events = capture_events()
    client.get(reverse("view_exc"), headers={"X_FORWARDED_HOST": "example.com"})

    (error,) = exceptions
    assert isinstance(error, ZeroDivisionError)

    (event,) = events
    assert event["request"]["url"] == "http://localhost/view-exc"


def test_middleware_exceptions(sentry_init, client, capture_exceptions):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    exceptions = capture_exceptions()
    client.get(reverse("middleware_exc"))

    (error,) = exceptions
    assert isinstance(error, ZeroDivisionError)


def test_request_captured(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    content, status, headers = client.get(reverse("message"))
    assert b"".join(content) == b"ok"

    (event,) = events
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

    (event,) = events

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

    (event,) = events

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

    (event,) = events

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    (frame,) = exception["stacktrace"]["frames"]
    assert frame["vars"]["my_queryset"].startswith(
        "<QuerySet from django.db.models.query at"
    )


def test_custom_error_handler_request_context(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    content, status, headers = client.post("/404")
    assert status.lower() == "404 not found"

    (event,) = events

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

    (event,) = events
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

    events = capture_events()

    sql = connection.cursor()

    with configure_scope() as scope:
        scope.clear_breadcrumbs()

    with pytest.raises(OperationalError):
        # table doesn't even exist
        sql.execute("""SELECT count(*) FROM people_person WHERE foo = %s""", [123])

    capture_message("HI")

    (event,) = events

    if with_integration:
        crumb = event["breadcrumbs"]["values"][-1]

        assert crumb["message"] == "SELECT count(*) FROM people_person WHERE foo = %s"
        assert crumb["data"]["db.params"] == [123]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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
    with configure_scope() as scope:
        scope.clear_breadcrumbs()

    with pytest.raises(ProgrammingError):
        sql.execute(
            """SELECT count(*) FROM people_person WHERE foo = %(my_foo)s""",
            {"my_foo": 10},
        )

    capture_message("HI")
    (event,) = events

    crumb = event["breadcrumbs"]["values"][-1]
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
@pytest_mark_django_db_decorator()
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

    with configure_scope() as scope:
        scope.clear_breadcrumbs()

    events = capture_events()

    with pytest.raises(ProgrammingError):
        sql.execute(query(psycopg2.sql), {"my_param": 10})

    capture_message("HI")

    (event,) = events
    crumb = event["breadcrumbs"]["values"][-1]
    assert crumb["message"] == ('SELECT %(my_param)s FROM "foobar"')
    assert crumb["data"]["db.params"] == {"my_param": 10}


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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
    with configure_scope() as scope:
        scope.clear_breadcrumbs()

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

    (event,) = events
    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"][-2:] == [
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


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_django_connect_trace(sentry_init, client, capture_events, render_span_tree):
    """
    Verify we record a span when opening a new database.
    """
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    from django.db import connections

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    content, status, headers = client.get(reverse("postgres_select"))
    assert status == "200 OK"

    assert '- op="db": description="connect"' in render_span_tree(events[0])


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_django_connect_breadcrumbs(
    sentry_init, client, capture_events, render_span_tree
):
    """
    Verify we record a breadcrumb when opening a new database.
    """
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
    )

    from django.db import connections

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    cursor = connections["postgres"].cursor()
    cursor.execute("select 1")

    # trigger recording of event.
    capture_message("HI")
    (event,) = events
    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"][-2:] == [
        {"message": "connect", "category": "query", "type": "default"},
        {"message": "select 1", "category": "query", "data": {}, "type": "default"},
    ]


@pytest.mark.parametrize(
    "transaction_style,client_url,expected_transaction,expected_source,expected_response",
    [
        (
            "function_name",
            "/message",
            "tests.integrations.django.myapp.views.message",
            "component",
            b"ok",
        ),
        ("url", "/message", "/message", "route", b"ok"),
        ("url", "/404", "/404", "url", b"404"),
    ],
)
def test_transaction_style(
    sentry_init,
    client,
    capture_events,
    transaction_style,
    client_url,
    expected_transaction,
    expected_source,
    expected_response,
):
    sentry_init(
        integrations=[DjangoIntegration(transaction_style=transaction_style)],
        send_default_pii=True,
    )
    events = capture_events()
    content, status, headers = client.get(client_url)
    assert b"".join(content) == expected_response

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


def test_request_body(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    content, status, headers = client.post(
        reverse("post_echo"), data=b"heyooo", content_type="text/plain"
    )
    assert status.lower() == "200 ok"
    assert b"".join(content) == b"heyooo"

    (event,) = events

    assert event["message"] == "hi"
    assert event["request"]["data"] == ""
    assert event["_meta"]["request"]["data"][""] == {
        "rem": [["!raw", "x"]],
    }

    del events[:]

    content, status, headers = client.post(
        reverse("post_echo"), data=b'{"hey": 42}', content_type="application/json"
    )
    assert status.lower() == "200 ok"
    assert b"".join(content) == b'{"hey": 42}'

    (event,) = events

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

    (event,) = events

    assert "data" not in event["request"]


@pytest.mark.parametrize("with_executing_integration", [[], [ExecutingIntegration()]])
def test_template_exception(
    sentry_init, client, capture_events, with_executing_integration
):
    sentry_init(integrations=[DjangoIntegration()] + with_executing_integration)
    events = capture_events()

    content, status, headers = client.get(reverse("template_exc"))
    assert status.lower() == "500 internal server error"

    (event,) = events
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

    if with_executing_integration:
        assert filenames[-3:] == [
            ("Parser.parse", "django.template.base"),
            (None, None),
            ("Parser.invalid_block_tag", "django.template.base"),
        ]
    else:
        assert filenames[-3:] == [
            ("parse", "django.template.base"),
            (None, None),
            ("invalid_block_tag", "django.template.base"),
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
        raise AssertionError("unreachable")

    (error,) = exceptions
    assert isinstance(error, ZeroDivisionError)

    (event,) = events
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


def test_render_spans(sentry_init, client, capture_events, render_span_tree):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
    views_tests = [
        (
            reverse("template_test2"),
            '- op="template.render": description="[user_name.html, ...]"',
        ),
    ]
    if DJANGO_VERSION >= (1, 7):
        views_tests.append(
            (
                reverse("template_test"),
                '- op="template.render": description="user_name.html"',
            ),
        )

    for url, expected_line in views_tests:
        events = capture_events()
        _content, status, _headers = client.get(url)
        transaction = events[0]
        assert expected_line in render_span_tree(transaction)


def test_middleware_spans(sentry_init, client, capture_events, render_span_tree):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    _content, status, _headers = client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"

    if DJANGO_VERSION >= (1, 10):
        assert (
            render_span_tree(transaction)
            == """\
- op="http.server": description=null
  - op="event.django": description="django.db.reset_queries"
  - op="event.django": description="django.db.close_old_connections"
  - op="middleware.django": description="django.contrib.sessions.middleware.SessionMiddleware.__call__"
    - op="middleware.django": description="django.contrib.auth.middleware.AuthenticationMiddleware.__call__"
      - op="middleware.django": description="middleware.django.csrf.CsrfViewMiddleware.__call__"
        - op="middleware.django": description="tests.integrations.django.myapp.settings.TestMiddleware.__call__"
          - op="middleware.django": description="tests.integrations.django.myapp.settings.TestFunctionMiddleware.__call__"
            - op="middleware.django": description="middleware.django.csrf.CsrfViewMiddleware.process_view"
            - op="view.django": description="message"\
"""
        )

    else:
        assert (
            render_span_tree(transaction)
            == """\
- op="http.server": description=null
  - op="event.django": description="django.db.reset_queries"
  - op="event.django": description="django.db.close_old_connections"
  - op="middleware.django": description="django.contrib.sessions.middleware.SessionMiddleware.process_request"
  - op="middleware.django": description="django.contrib.auth.middleware.AuthenticationMiddleware.process_request"
  - op="middleware.django": description="tests.integrations.django.myapp.settings.TestMiddleware.process_request"
  - op="middleware.django": description="middleware.django.csrf.CsrfViewMiddleware.process_view"
  - op="view.django": description="message"
  - op="middleware.django": description="tests.integrations.django.myapp.settings.TestMiddleware.process_response"
  - op="middleware.django": description="middleware.django.csrf.CsrfViewMiddleware.process_response"
  - op="middleware.django": description="django.contrib.sessions.middleware.SessionMiddleware.process_response"\
"""
        )


def test_middleware_spans_disabled(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(middleware_spans=False)], traces_sample_rate=1.0
    )
    events = capture_events()

    _content, status, _headers = client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"

    assert len(transaction["spans"]) == 2

    assert transaction["spans"][0]["op"] == "event.django"
    assert transaction["spans"][0]["description"] == "django.db.reset_queries"

    assert transaction["spans"][1]["op"] == "event.django"
    assert transaction["spans"][1]["description"] == "django.db.close_old_connections"


def test_csrf(sentry_init, client):
    """
    Assert that CSRF view decorator works even with the view wrapped in our own
    callable.
    """

    sentry_init(integrations=[DjangoIntegration()])

    content, status, _headers = client.post(reverse("csrf_hello_not_exempt"))
    assert status.lower() == "403 forbidden"

    content, status, _headers = client.post(reverse("sentryclass_csrf"))
    assert status.lower() == "403 forbidden"

    content, status, _headers = client.post(reverse("sentryclass"))
    assert status.lower() == "200 ok"
    assert b"".join(content) == b"ok"

    content, status, _headers = client.post(reverse("classbased"))
    assert status.lower() == "200 ok"
    assert b"".join(content) == b"ok"

    content, status, _headers = client.post(reverse("message"))
    assert status.lower() == "200 ok"
    assert b"".join(content) == b"ok"


@pytest.mark.skipif(DJANGO_VERSION < (2, 0), reason="Requires Django > 2.0")
def test_custom_urlconf_middleware(
    settings, sentry_init, client, capture_events, render_span_tree
):
    """
    Some middlewares (for instance in django-tenants) overwrite request.urlconf.
    Test that the resolver picks up the correct urlconf for transaction naming.
    """
    urlconf = "tests.integrations.django.myapp.middleware.custom_urlconf_middleware"
    settings.ROOT_URLCONF = ""
    settings.MIDDLEWARE.insert(0, urlconf)
    client.application.load_middleware()

    sentry_init(integrations=[DjangoIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    content, status, _headers = client.get("/custom/ok")
    assert status.lower() == "200 ok"
    assert b"".join(content) == b"custom ok"

    event = events.pop(0)
    assert event["transaction"] == "/custom/ok"
    assert "custom_urlconf_middleware" in render_span_tree(event)

    _content, status, _headers = client.get("/custom/exc")
    assert status.lower() == "500 internal server error"

    error_event, transaction_event = events
    assert error_event["transaction"] == "/custom/exc"
    assert error_event["exception"]["values"][-1]["mechanism"]["type"] == "django"
    assert transaction_event["transaction"] == "/custom/exc"
    assert "custom_urlconf_middleware" in render_span_tree(transaction_event)

    settings.MIDDLEWARE.pop(0)


def test_get_receiver_name():
    def dummy(a, b):
        return a + b

    name = _get_receiver_name(dummy)

    if PY2:
        assert name == "tests.integrations.django.test_basic.dummy"
    else:
        assert (
            name
            == "tests.integrations.django.test_basic.test_get_receiver_name.<locals>.dummy"
        )

    a_partial = partial(dummy)
    name = _get_receiver_name(a_partial)
    assert name == str(a_partial)

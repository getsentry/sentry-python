import inspect
import json
import os
import pytest
import re
import sys
import time
from functools import partial
from unittest.mock import patch

from werkzeug.test import Client

from django import VERSION as DJANGO_VERSION

from django.contrib.auth.models import User
from django.core.management import execute_from_command_line
from django.db.utils import OperationalError, ProgrammingError, DataError
from django.http.request import RawPostDataException
from django.template.context import make_context
from django.utils.functional import SimpleLazyObject

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

import sentry_sdk
from sentry_sdk._compat import PY310
from sentry_sdk import capture_message, capture_exception
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import (
    DjangoIntegration,
    DjangoRequestExtractor,
    _set_db_data,
)
from sentry_sdk.integrations.django.signals_handlers import _get_receiver_name
from sentry_sdk.integrations.executing import ExecutingIntegration
from sentry_sdk.profiler.utils import get_frame_name
from sentry_sdk.tracing import Span
from tests.conftest import unpack_werkzeug_response
from tests.integrations.django.myapp.wsgi import application
from tests.integrations.django.myapp.signals import myapp_custom_signal_silenced
from tests.integrations.django.utils import pytest_mark_django_db_decorator

DJANGO_VERSION = DJANGO_VERSION[:2]


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
    content, status, headers = unpack_werkzeug_response(client.get(reverse("message")))

    assert content == b"ok"

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
    content, status, headers = unpack_werkzeug_response(
        client.head(reverse("classbased"))
    )
    assert status.lower() == "200 ok"

    (event,) = events

    assert (
        event["transaction"] == "tests.integrations.django.myapp.views.ClassBasedView"
    )
    assert event["message"] == "hi"


def test_has_trace_if_performance_enabled(sentry_init, client, capture_events):
    sentry_init(
        integrations=[
            DjangoIntegration(
                http_methods_to_capture=("HEAD",),
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()
    client.head(reverse("view_exc_with_msg"))

    (msg_event, error_event, transaction_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert transaction_event["contexts"]["trace"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
    )


def test_has_trace_if_performance_disabled(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
    )
    events = capture_events()
    client.head(reverse("view_exc_with_msg"))

    (msg_event, error_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
    )


def test_trace_from_headers_if_performance_enabled(sentry_init, client, capture_events):
    sentry_init(
        integrations=[
            DjangoIntegration(
                http_methods_to_capture=("HEAD",),
            )
        ],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    client.head(
        reverse("view_exc_with_msg"), headers={"sentry-trace": sentry_trace_header}
    )

    (msg_event, error_event, transaction_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert transaction_event["contexts"]["trace"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id
    assert transaction_event["contexts"]["trace"]["trace_id"] == trace_id


def test_trace_from_headers_if_performance_disabled(
    sentry_init, client, capture_events
):
    sentry_init(
        integrations=[
            DjangoIntegration(
                http_methods_to_capture=("HEAD",),
            )
        ],
    )

    events = capture_events()

    trace_id = "582b43a4192642f0b136d5159a501701"
    sentry_trace_header = "{}-{}-{}".format(trace_id, "6e8f22c393e68f19", 1)

    client.head(
        reverse("view_exc_with_msg"), headers={"sentry-trace": sentry_trace_header}
    )

    (msg_event, error_event) = events

    assert msg_event["contexts"]["trace"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert error_event["contexts"]["trace"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert msg_event["contexts"]["trace"]["trace_id"] == trace_id
    assert error_event["contexts"]["trace"]["trace_id"] == trace_id


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_user_captured(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    content, status, headers = unpack_werkzeug_response(client.get(reverse("mylogin")))
    assert content == b"ok"

    assert not events

    content, status, headers = unpack_werkzeug_response(client.get(reverse("message")))
    assert content == b"ok"

    (event,) = events

    assert event["user"] == {
        "email": "lennon@thebeatles.com",
        "username": "john",
        "id": "1",
    }


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_context_nested_queryset_repr(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    User.objects.create_user("john", "lennon@thebeatles.com", "johnpassword")

    try:
        context = make_context({"entries": User.objects.all()})  # noqa
        1 / 0
    except Exception:
        capture_exception()

    (event,) = events

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    (frame,) = exception["stacktrace"]["frames"]
    assert "<User: " not in frame["vars"]["context"]


def test_custom_error_handler_request_context(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    content, status, headers = unpack_werkzeug_response(client.post("/404"))
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


def test_500(sentry_init, client):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)

    content, status, headers = unpack_werkzeug_response(client.get("/view-exc"))
    assert status.lower() == "500 internal server error"
    content = content.decode("utf-8")

    assert content == "Sentry error."


@pytest.mark.forked
def test_management_command_raises():
    # This just checks for our assumption that Django passes through all
    # exceptions by default, so our excepthook can be used for management
    # commands.
    with pytest.raises(ZeroDivisionError):
        execute_from_command_line(["manage.py", "mycrash"])


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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

    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

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
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

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


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_response_trace(sentry_init, client, capture_events, render_span_tree):
    pytest.importorskip("rest_framework")
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()
    content, status, headers = unpack_werkzeug_response(
        client.get(reverse("rest_json_response"))
    )
    assert status == "200 OK"

    assert (
        '- op="view.response.render": description="serialize response"'
        in render_span_tree(events[0])
    )


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

    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

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
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

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

    content, status, headers = unpack_werkzeug_response(
        client.get(reverse("postgres_select"))
    )
    assert status == "200 OK"

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db":
            data = span.get("data")
            assert data.get(SPANDATA.DB_SYSTEM) == "postgresql"

    assert '- op="db": description="connect"' in render_span_tree(event)


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_django_connect_breadcrumbs(sentry_init, capture_events):
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


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_connection_span_data(sentry_init, client, capture_events):
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

    content, status, headers = unpack_werkzeug_response(
        client.get(reverse("postgres_select"))
    )
    assert status == "200 OK"

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db":
            data = span.get("data")
            assert data.get(SPANDATA.DB_SYSTEM) == "postgresql"
            conn_params = connections["postgres"].get_connection_params()
            assert data.get(SPANDATA.DB_NAME) is not None
            assert data.get(SPANDATA.DB_NAME) == conn_params.get(
                "database"
            ) or conn_params.get("dbname")
            assert data.get(SPANDATA.SERVER_ADDRESS) == os.environ.get(
                "SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"
            )
            assert data.get(SPANDATA.SERVER_PORT) == os.environ.get(
                "SENTRY_PYTHON_TEST_POSTGRES_PORT", "5432"
            )


def test_set_db_data_custom_backend():
    class DummyBackend:
        # https://github.com/mongodb/mongo-python-driver/blob/6ffae5522c960252b8c9adfe2a19b29ff28187cb/pymongo/collection.py#L126
        def __getattr__(self, attr):
            return self

        def __call__(self):
            raise TypeError

        def get_connection_params(self):
            return {}

    try:
        _set_db_data(Span(), DummyBackend())
    except TypeError:
        pytest.fail("A TypeError was raised")


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
    content, status, headers = unpack_werkzeug_response(client.get(client_url))
    assert content == expected_response

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


def test_request_body(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()
    content, status, headers = unpack_werkzeug_response(
        client.post(reverse("post_echo"), data=b"heyooo", content_type="text/plain")
    )
    assert status.lower() == "200 ok"
    assert content == b"heyooo"

    (event,) = events

    assert event["message"] == "hi"
    assert event["request"]["data"] == ""
    assert event["_meta"]["request"]["data"][""] == {
        "rem": [["!raw", "x"]],
    }

    del events[:]

    content, status, headers = unpack_werkzeug_response(
        client.post(
            reverse("post_echo"), data=b'{"hey": 42}', content_type="application/json"
        )
    )
    assert status.lower() == "200 ok"
    assert content == b'{"hey": 42}'

    (event,) = events

    assert event["message"] == "hi"
    assert event["request"]["data"] == {"hey": 42}
    assert "" not in event


@pytest.mark.xfail
def test_read_request(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()

    content, status, headers = unpack_werkzeug_response(
        client.post(
            reverse("read_body_and_view_exc"),
            data=b'{"hey": 42}',
            content_type="application/json",
        )
    )

    assert status.lower() == "500 internal server error"

    (event,) = events

    assert "data" not in event["request"]


def test_request_body_already_read(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])

    events = capture_events()

    class MockExtractor(DjangoRequestExtractor):
        def raw_data(self):
            raise RawPostDataException

    with patch("sentry_sdk.integrations.django.DjangoRequestExtractor", MockExtractor):
        client.post(
            reverse("post_echo"), data=b'{"hey": 42}', content_type="application/json"
        )

        (event,) = events

        assert event["message"] == "hi"
        assert "data" not in event["request"]


def test_template_tracing_meta(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()])
    events = capture_events()

    content, _, _ = unpack_werkzeug_response(client.get(reverse("template_test3")))
    rendered_meta = content.decode("utf-8")

    traceparent, baggage = events[0]["message"].split("\n")
    assert traceparent != ""
    assert baggage != ""

    match = re.match(
        r'^<meta name="sentry-trace" content="([^\"]*)"><meta name="baggage" content="([^\"]*)">\n',
        rendered_meta,
    )
    assert match is not None
    assert match.group(1) == traceparent

    rendered_baggage = match.group(2)
    assert rendered_baggage == baggage


@pytest.mark.parametrize("with_executing_integration", [[], [ExecutingIntegration()]])
def test_template_exception(
    sentry_init, client, capture_events, with_executing_integration
):
    sentry_init(integrations=[DjangoIntegration()] + with_executing_integration)
    events = capture_events()

    content, status, headers = unpack_werkzeug_response(
        client.get(reverse("template_exc"))
    )
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

    _, status, _ = unpack_werkzeug_response(client.get(reverse(endpoint)))
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
        client.get(url)
        transaction = events[0]
        assert expected_line in render_span_tree(transaction)


@pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="Requires Django >= 1.9")
def test_render_spans_complex_context(sentry_init, client, capture_events):
    sentry_init(
        integrations=[
            DjangoIntegration(
                cache_spans=False,
                middleware_spans=False,
                signals_spans=False,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    begin = time.time()
    client.get(reverse("template_test4"))

    (transaction,) = events
    end = time.time()

    # evaluating the complex context takes 10 seconds, (see lambda in template_test4)
    # so we expect the total time to be way less, because the complex context is not evaluated
    assert end - begin < 10 / 5

    # Make sure complex items are not put into the span.data
    assert transaction["spans"][-1]["data"]["context"] == {
        "user_age": 25,
        "complex_dict": {
            "a": 1,
        },
        "complex_list": [1, 2, 3],
        "none_context": None,
    }


if DJANGO_VERSION >= (1, 10):
    EXPECTED_MIDDLEWARE_SPANS = """\
- op="http.server": description=null
  - op="middleware.django": description="django.contrib.sessions.middleware.SessionMiddleware.__call__"
    - op="middleware.django": description="django.contrib.auth.middleware.AuthenticationMiddleware.__call__"
      - op="middleware.django": description="django.middleware.csrf.CsrfViewMiddleware.__call__"
        - op="middleware.django": description="tests.integrations.django.myapp.settings.TestMiddleware.__call__"
          - op="middleware.django": description="tests.integrations.django.myapp.settings.TestFunctionMiddleware.__call__"
            - op="middleware.django": description="django.middleware.csrf.CsrfViewMiddleware.process_view"
            - op="view.render": description="message"\
"""
else:
    EXPECTED_MIDDLEWARE_SPANS = """\
- op="http.server": description=null
  - op="middleware.django": description="django.contrib.sessions.middleware.SessionMiddleware.process_request"
  - op="middleware.django": description="django.contrib.auth.middleware.AuthenticationMiddleware.process_request"
  - op="middleware.django": description="tests.integrations.django.myapp.settings.TestMiddleware.process_request"
  - op="middleware.django": description="django.middleware.csrf.CsrfViewMiddleware.process_view"
  - op="view.render": description="message"
  - op="middleware.django": description="tests.integrations.django.myapp.settings.TestMiddleware.process_response"
  - op="middleware.django": description="django.middleware.csrf.CsrfViewMiddleware.process_response"
  - op="middleware.django": description="django.contrib.sessions.middleware.SessionMiddleware.process_response"\
"""


def test_middleware_spans(sentry_init, client, capture_events, render_span_tree):
    sentry_init(
        integrations=[
            DjangoIntegration(signals_spans=False),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"
    assert render_span_tree(transaction) == EXPECTED_MIDDLEWARE_SPANS


def test_middleware_spans_disabled(sentry_init, client, capture_events):
    sentry_init(
        integrations=[
            DjangoIntegration(middleware_spans=False, signals_spans=False),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"
    assert not len(transaction["spans"])


EXPECTED_SIGNALS_SPANS = """\
- op="http.server": description=null
  - op="event.django": description="django.db.reset_queries"
  - op="event.django": description="django.db.close_old_connections"\
"""


def test_signals_spans(sentry_init, client, capture_events, render_span_tree):
    sentry_init(
        integrations=[
            DjangoIntegration(middleware_spans=False),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"
    assert render_span_tree(transaction) == EXPECTED_SIGNALS_SPANS

    assert transaction["spans"][0]["op"] == "event.django"
    assert transaction["spans"][0]["description"] == "django.db.reset_queries"

    assert transaction["spans"][1]["op"] == "event.django"
    assert transaction["spans"][1]["description"] == "django.db.close_old_connections"


def test_signals_spans_disabled(sentry_init, client, capture_events):
    sentry_init(
        integrations=[
            DjangoIntegration(middleware_spans=False, signals_spans=False),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("message"))

    message, transaction = events

    assert message["message"] == "hi"
    assert not transaction["spans"]


EXPECTED_SIGNALS_SPANS_FILTERED = """\
- op="http.server": description=null
  - op="event.django": description="django.db.reset_queries"
  - op="event.django": description="django.db.close_old_connections"
  - op="event.django": description="tests.integrations.django.myapp.signals.signal_handler"\
"""


def test_signals_spans_filtering(sentry_init, client, capture_events, render_span_tree):
    sentry_init(
        integrations=[
            DjangoIntegration(
                middleware_spans=False,
                signals_denylist=[
                    myapp_custom_signal_silenced,
                ],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("send_myapp_custom_signal"))

    (transaction,) = events

    assert render_span_tree(transaction) == EXPECTED_SIGNALS_SPANS_FILTERED

    assert transaction["spans"][0]["op"] == "event.django"
    assert transaction["spans"][0]["description"] == "django.db.reset_queries"

    assert transaction["spans"][1]["op"] == "event.django"
    assert transaction["spans"][1]["description"] == "django.db.close_old_connections"

    assert transaction["spans"][2]["op"] == "event.django"
    assert (
        transaction["spans"][2]["description"]
        == "tests.integrations.django.myapp.signals.signal_handler"
    )


def test_csrf(sentry_init, client):
    """
    Assert that CSRF view decorator works even with the view wrapped in our own
    callable.
    """

    sentry_init(integrations=[DjangoIntegration()])

    content, status, _headers = unpack_werkzeug_response(
        client.post(reverse("csrf_hello_not_exempt"))
    )
    assert status.lower() == "403 forbidden"

    content, status, _headers = unpack_werkzeug_response(
        client.post(reverse("sentryclass_csrf"))
    )
    assert status.lower() == "403 forbidden"

    content, status, _headers = unpack_werkzeug_response(
        client.post(reverse("sentryclass"))
    )
    assert status.lower() == "200 ok"
    assert content == b"ok"

    content, status, _headers = unpack_werkzeug_response(
        client.post(reverse("classbased"))
    )
    assert status.lower() == "200 ok"
    assert content == b"ok"

    content, status, _headers = unpack_werkzeug_response(
        client.post(reverse("message"))
    )
    assert status.lower() == "200 ok"
    assert content == b"ok"


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

    content, status, _headers = unpack_werkzeug_response(client.get("/custom/ok"))
    assert status.lower() == "200 ok"
    assert content == b"custom ok"

    event = events.pop(0)
    assert event["transaction"] == "/custom/ok"
    assert "custom_urlconf_middleware" in render_span_tree(event)

    _content, status, _headers = unpack_werkzeug_response(client.get("/custom/exc"))
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

    assert (
        name
        == "tests.integrations.django.test_basic.test_get_receiver_name.<locals>.dummy"
    )

    a_partial = partial(dummy)
    name = _get_receiver_name(a_partial)
    if PY310:
        assert name == "functools.partial(<function " + a_partial.func.__name__ + ">)"
    else:
        assert name == "partial(<function " + a_partial.func.__name__ + ">)"


@pytest.mark.skipif(DJANGO_VERSION <= (1, 11), reason="Requires Django > 1.11")
def test_span_origin(sentry_init, client, capture_events):
    sentry_init(
        integrations=[
            DjangoIntegration(
                middleware_spans=True,
                signals_spans=True,
                cache_spans=True,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("view_with_signal"))

    (transaction,) = events

    assert transaction["contexts"]["trace"]["origin"] == "auto.http.django"

    signal_span_found = False
    for span in transaction["spans"]:
        assert span["origin"] == "auto.http.django"
        if span["op"] == "event.django":
            signal_span_found = True

    assert signal_span_found


def test_transaction_http_method_default(sentry_init, client, capture_events):
    """
    By default OPTIONS and HEAD requests do not create a transaction.
    """
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    (event,) = events

    assert len(events) == 1
    assert event["request"]["method"] == "GET"


def test_transaction_http_method_custom(sentry_init, client, capture_events):
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

    client.get("/nomessage")
    client.options("/nomessage")
    client.head("/nomessage")

    assert len(events) == 2

    (event1, event2) = events
    assert event1["request"]["method"] == "OPTIONS"
    assert event2["request"]["method"] == "HEAD"


def test_ensures_spotlight_middleware_when_spotlight_is_enabled(sentry_init, settings):
    """
    Test that ensures if Spotlight is enabled, relevant SpotlightMiddleware
    is added to middleware list in settings.
    """
    settings.DEBUG = True
    original_middleware = frozenset(settings.MIDDLEWARE)

    sentry_init(integrations=[DjangoIntegration()], spotlight=True)

    added = frozenset(settings.MIDDLEWARE) ^ original_middleware

    assert "sentry_sdk.spotlight.SpotlightMiddleware" in added


def test_ensures_no_spotlight_middleware_when_env_killswitch_is_false(
    monkeypatch, sentry_init, settings
):
    """
    Test that ensures if Spotlight is enabled, but is set to a falsy value
    the relevant SpotlightMiddleware is NOT added to middleware list in settings.
    """
    settings.DEBUG = True
    monkeypatch.setenv("SENTRY_SPOTLIGHT_ON_ERROR", "no")

    original_middleware = frozenset(settings.MIDDLEWARE)

    sentry_init(integrations=[DjangoIntegration()], spotlight=True)

    added = frozenset(settings.MIDDLEWARE) ^ original_middleware

    assert "sentry_sdk.spotlight.SpotlightMiddleware" not in added


def test_ensures_no_spotlight_middleware_when_no_spotlight(
    monkeypatch, sentry_init, settings
):
    """
    Test that ensures if Spotlight is not enabled
    the relevant SpotlightMiddleware is NOT added to middleware list in settings.
    """
    settings.DEBUG = True

    # We should NOT have the middleware even if the env var is truthy if Spotlight is off
    monkeypatch.setenv("SENTRY_SPOTLIGHT_ON_ERROR", "1")

    original_middleware = frozenset(settings.MIDDLEWARE)

    sentry_init(integrations=[DjangoIntegration()], spotlight=False)

    added = frozenset(settings.MIDDLEWARE) ^ original_middleware

    assert "sentry_sdk.spotlight.SpotlightMiddleware" not in added


def test_get_frame_name_when_in_lazy_object():
    allowed_to_init = False

    class SimpleLazyObjectWrapper(SimpleLazyObject):
        def unproxied_method(self):
            """
            For testing purposes. We inject a method on the SimpleLazyObject
            class so if python is executing this method, we should get
            this class instead of the wrapped class and avoid evaluating
            the wrapped object too early.
            """
            return inspect.currentframe()

    class GetFrame:
        def __init__(self):
            assert allowed_to_init, "GetFrame not permitted to initialize yet"

        def proxied_method(self):
            """
            For testing purposes. We add an proxied method on the instance
            class so if python is executing this method, we should get
            this class instead of the wrapper class.
            """
            return inspect.currentframe()

    instance = SimpleLazyObjectWrapper(lambda: GetFrame())

    assert get_frame_name(instance.unproxied_method()) == (
        "SimpleLazyObjectWrapper.unproxied_method"
        if sys.version_info < (3, 11)
        else "test_get_frame_name_when_in_lazy_object.<locals>.SimpleLazyObjectWrapper.unproxied_method"
    )

    # Now that we're about to access an instance method on the wrapped class,
    # we should permit initializing it
    allowed_to_init = True

    assert get_frame_name(instance.proxied_method()) == (
        "GetFrame.proxied_method"
        if sys.version_info < (3, 11)
        else "test_get_frame_name_when_in_lazy_object.<locals>.GetFrame.proxied_method"
    )

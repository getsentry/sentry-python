import pytest

from werkzeug.test import Client
from django.core.management import execute_from_command_line


try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk import last_event_id, capture_message
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
    assert event["transaction"] == "tests.integrations.django.myapp.views.message"
    assert event["request"] == {
        "cookies": {},
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Content-Length": "0", "Content-Type": "", "Host": "localhost"},
        "method": "GET",
        "query_string": "",
        "url": "http://localhost/message",
    }


def test_transaction_with_class_view(sentry_init, client, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    content, status, headers = client.head(reverse("classbased"))
    assert status.lower() == "200 ok"

    event, = events

    assert (
        event["transaction"] == "tests.integrations.django.myapp.views.ClassBasedView"
    )
    assert event["message"] == "hi"


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

    assert event["user"] == {"email": "lennon@thebeatles.com", "username": "john"}


def test_404(sentry_init, client):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    content, status, headers = client.get("/404")
    assert status.lower() == "404 not found"


def test_500(sentry_init, client):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    old_event_id = last_event_id()
    content, status, headers = client.get("/view-exc")
    assert status.lower() == "500 internal server error"
    content = b"".join(content).decode("utf-8")
    event_id = last_event_id()
    assert content == "Sentry error: %s" % event_id
    assert event_id is not None
    assert old_event_id != event_id


def test_management_command_raises():
    # This just checks for our assumption that Django passes through all
    # exceptions by default, so our excepthook can be used for management
    # commands.
    with pytest.raises(ZeroDivisionError):
        execute_from_command_line(["manage.py", "mycrash"])


@pytest.mark.django_db
def test_sql_queries(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    from django.db import connection

    sql = connection.cursor()

    events = capture_events()
    with pytest.raises(Exception):
        # table doesn't even exist
        sql.execute("""SELECT count(*) FROM people_person WHERE foo = %s""", [123])

    capture_message("HI")

    event, = events

    crumb, = event["breadcrumbs"]

    assert crumb["message"] == """SELECT count(*) FROM people_person WHERE foo = 123"""


@pytest.mark.django_db
def test_sql_queries_large_params(sentry_init, capture_events):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    from django.db import connection

    sql = connection.cursor()

    events = capture_events()
    with pytest.raises(Exception):
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

import pytest

django = pytest.importorskip("django")


from tests.integrations.django.myapp.wsgi import application
from werkzeug.test import Client

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk import Hub


@pytest.fixture
def client(monkeypatch_test_transport):
    monkeypatch_test_transport(Hub.current.client)
    return Client(application)


def test_view_exceptions(client, capture_exceptions):
    exceptions = capture_exceptions()
    client.get(reverse("view_exc"))

    error, = exceptions
    assert isinstance(error, ZeroDivisionError)


def test_middleware_exceptions(client, capture_exceptions):
    exceptions = capture_exceptions()
    client.get(reverse("middleware_exc"))

    error, = exceptions
    assert isinstance(error, ZeroDivisionError)


def test_request_captured(client, capture_events):
    events = capture_events()
    content, status, headers = client.get(reverse("message"))
    assert b"".join(content) == b"ok"

    event, = events
    assert event["transaction"] == "message"
    assert event["request"] == {
        "cookies": {},
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Content-Length": "0", "Content-Type": "", "Host": "localhost"},
        "method": "GET",
        "query_string": "",
        "url": "http://localhost/message",
    }


@pytest.mark.django_db
def test_user_captured(client, capture_events):
    events = capture_events()
    content, status, headers = client.get(reverse("mylogin"))
    assert b"".join(content) == b"ok"

    assert not events

    content, status, headers = client.get(reverse("message"))
    assert b"".join(content) == b"ok"

    event, = events

    assert event["user"] == {"email": "lennon@thebeatles.com", "username": "john"}


def test_404(client):
    content, status, headers = client.get("/404")
    assert status.lower() == "404 not found"

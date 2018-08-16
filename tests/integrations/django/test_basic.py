import pytest

django = pytest.importorskip("django")


from django.test import Client
from django.core.management import execute_from_command_line

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk import Hub


@pytest.fixture
def client(monkeypatch_test_transport):
    monkeypatch_test_transport(Hub.current.client)
    return Client()


def test_view_exceptions(client, capture_exceptions):
    exceptions = capture_exceptions()
    with pytest.raises(ZeroDivisionError) as exc:
        client.get(reverse("view_exc"))

    assert exceptions == [exc.value]


def test_middleware_exceptions(client, capture_exceptions):
    exceptions = capture_exceptions()
    with pytest.raises(ZeroDivisionError) as exc:
        client.get(reverse("middleware_exc"))

    assert exceptions == [exc.value]


def test_request_captured(client, capture_events):
    events = capture_events()
    response = client.get(reverse("message"))
    assert response.content == b"ok"

    event, = events
    assert event["transaction"] == "message"
    assert event["request"] == {
        "cookies": {},
        "env": {
            "REMOTE_ADDR": "127.0.0.1",
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
        },
        "headers": {"Cookie": ""},
        "method": "GET",
        "query_string": "",
        "url": "http://testserver/message",
    }


@pytest.mark.django_db
def test_user_captured(client, capture_events):
    events = capture_events()
    response = client.get(reverse("mylogin"))
    assert response.content == b"ok"

    assert not events

    response = client.get(reverse("message"))
    assert response.content == b"ok"

    event, = events

    assert event["user"] == {
        "email": "lennon@thebeatles.com",
        "ip_address": "127.0.0.1",
        "username": "john",
    }


def test_404(client):
    response = client.get("/404")
    assert response.status_code == 404


def test_management_command_raises():
    # This just checks for our assumption that Django passes through all
    # exceptions by default, so our excepthook can be used for management
    # commands.
    with pytest.raises(ZeroDivisionError):
        execute_from_command_line(["manage.py", "mycrash"])

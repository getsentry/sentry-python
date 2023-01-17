import pytest

from werkzeug.test import Client

from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.myapp.wsgi import application

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse


@pytest.fixture
def client():
    return Client(application)


def test_scrub_django_session_cookies_removed(
    sentry_init,
    client,
    capture_events,
):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=False)
    events = capture_events()
    client.set_cookie("localhost", "sessionid", "123")
    client.set_cookie("localhost", "csrftoken", "456")
    client.set_cookie("localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = events
    assert "cookies" not in event["request"]


def test_scrub_django_session_cookies_filtered(
    sentry_init,
    client,
    capture_events,
):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    client.set_cookie("localhost", "sessionid", "123")
    client.set_cookie("localhost", "csrftoken", "456")
    client.set_cookie("localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = events
    assert event["request"]["cookies"] == {
        "sessionid": "[Filtered]",
        "csrftoken": "[Filtered]",
        "foo": "bar",
    }


def test_scrub_django_custom_session_cookies_filtered(
    sentry_init,
    client,
    capture_events,
    settings,
):
    settings.SESSION_COOKIE_NAME = "my_sess"
    settings.CSRF_COOKIE_NAME = "csrf_secret"

    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    client.set_cookie("localhost", "my_sess", "123")
    client.set_cookie("localhost", "csrf_secret", "456")
    client.set_cookie("localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = events
    assert event["request"]["cookies"] == {
        "my_sess": "[Filtered]",
        "csrf_secret": "[Filtered]",
        "foo": "bar",
    }

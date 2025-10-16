import pytest

from werkzeug.test import Client

from sentry_sdk.integrations.django import DjangoIntegration
from tests.conftest import werkzeug_set_cookie
from tests.integrations.django.myapp.wsgi import application
from tests.integrations.django.utils import pytest_mark_django_db_decorator

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse


@pytest.fixture
def client():
    return Client(application)


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_scrub_django_session_cookies_removed(
    sentry_init,
    client,
    capture_events,
):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=False)
    events = capture_events()
    werkzeug_set_cookie(client, "localhost", "sessionid", "123")
    werkzeug_set_cookie(client, "localhost", "csrftoken", "456")
    werkzeug_set_cookie(client, "localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = events
    assert "cookies" not in event["request"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_scrub_django_session_cookies_filtered(
    sentry_init,
    client,
    capture_events,
):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()
    werkzeug_set_cookie(client, "localhost", "sessionid", "123")
    werkzeug_set_cookie(client, "localhost", "csrftoken", "456")
    werkzeug_set_cookie(client, "localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = events
    assert event["request"]["cookies"] == {
        "sessionid": "[Filtered]",
        "csrftoken": "[Filtered]",
        "foo": "bar",
    }


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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
    werkzeug_set_cookie(client, "localhost", "my_sess", "123")
    werkzeug_set_cookie(client, "localhost", "csrf_secret", "456")
    werkzeug_set_cookie(client, "localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = events
    assert event["request"]["cookies"] == {
        "my_sess": "[Filtered]",
        "csrf_secret": "[Filtered]",
        "foo": "bar",
    }

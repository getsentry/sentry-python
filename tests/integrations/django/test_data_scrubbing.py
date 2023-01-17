from functools import partial
import pytest
import pytest_django

from werkzeug.test import Client

from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.myapp.wsgi import application

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse


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


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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

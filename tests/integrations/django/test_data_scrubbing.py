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


NO_COOKIES = object()


@pytest.fixture
def client():
    return Client(application)


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize("span_streaming", [True, False])
def test_scrub_django_session_cookies_removed(
    sentry_init,
    client,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=False,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    items = capture_items("event")
    werkzeug_set_cookie(client, "localhost", "sessionid", "123")
    werkzeug_set_cookie(client, "localhost", "csrftoken", "456")
    werkzeug_set_cookie(client, "localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = (item.payload for item in items if item.type == "event")
    assert "cookies" not in event["request"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize("span_streaming", [True, False])
def test_scrub_django_session_cookies_filtered(
    sentry_init,
    client,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    items = capture_items("event")
    werkzeug_set_cookie(client, "localhost", "sessionid", "123")
    werkzeug_set_cookie(client, "localhost", "csrftoken", "456")
    werkzeug_set_cookie(client, "localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = (item.payload for item in items if item.type == "event")
    assert event["request"]["cookies"] == {
        "sessionid": "[Filtered]",
        "csrftoken": "[Filtered]",
        "foo": "bar",
    }


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize("span_streaming", [True, False])
def test_scrub_django_custom_session_cookies_filtered(
    sentry_init,
    client,
    capture_items,
    settings,
    span_streaming,
):
    settings.SESSION_COOKIE_NAME = "my_sess"
    settings.CSRF_COOKIE_NAME = "csrf_secret"

    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    items = capture_items("event")
    werkzeug_set_cookie(client, "localhost", "my_sess", "123")
    werkzeug_set_cookie(client, "localhost", "csrf_secret", "456")
    werkzeug_set_cookie(client, "localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = (item.payload for item in items if item.type == "event")
    assert event["request"]["cookies"] == {
        "my_sess": "[Filtered]",
        "csrf_secret": "[Filtered]",
        "foo": "bar",
    }


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize(
    "cookies_to_set, data_collection, expected_cookies",
    [
        pytest.param(
            {"sessionid": "123", "csrftoken": "456", "foo": "bar"},
            {"cookies": {"mode": "off"}},
            NO_COOKIES,
            id="off",
        ),
        pytest.param(
            {"sessionid": "123", "csrftoken": "456", "foo": "bar"},
            {"cookies": {"mode": "denylist"}},
            {
                "sessionid": "[Filtered]",
                "csrftoken": "[Filtered]",
                "foo": "bar",
            },
            id="denylist-default",
        ),
        pytest.param(
            {"sessionid": "123", "csrftoken": "456", "foo": "bar"},
            {"cookies": {"mode": "denylist", "terms": ["foo"]}},
            {
                "sessionid": "[Filtered]",
                "csrftoken": "[Filtered]",
                "foo": "[Filtered]",
            },
            id="denylist-extra-terms",
        ),
        pytest.param(
            {"sessionid": "123", "csrftoken": "456", "foo": "bar", "bar": "baz"},
            {"cookies": {"mode": "allowlist", "terms": ["foo"]}},
            {
                "sessionid": "[Filtered]",
                "csrftoken": "[Filtered]",
                "foo": "bar",
                "bar": "[Filtered]",
            },
            id="allowlist",
        ),
        pytest.param(
            {"sessionid": "123", "csrftoken": "456", "foo": "bar", "bar": "baz"},
            {"cookies": {"mode": "allowlist", "terms": ["sessionid", "foo"]}},
            {
                "sessionid": "[Filtered]",
                "csrftoken": "[Filtered]",
                "foo": "bar",
                "bar": "[Filtered]",
            },
            id="allowlist-cannot-override-sensitive",
        ),
        pytest.param(
            {"sessionid": "123", "csrftoken": "456", "foo": "bar"},
            {},
            {
                "sessionid": "[Filtered]",
                "csrftoken": "[Filtered]",
                "foo": "bar",
            },
            id="cookies-omitted-defaults-to-denylist",
        ),
    ],
)
def test_data_collection_cookies(
    sentry_init,
    client,
    capture_items,
    cookies_to_set,
    data_collection,
    expected_cookies,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        _experiments={"data_collection": data_collection},
    )
    items = capture_items("event")
    for name, value in cookies_to_set.items():
        werkzeug_set_cookie(client, "localhost", name, value)
    client.get(reverse("view_exc"))

    (event,) = (item.payload for item in items if item.type == "event")
    if expected_cookies is NO_COOKIES:
        assert "cookies" not in event["request"]
    else:
        assert event["request"]["cookies"] == expected_cookies


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_data_collection_cookies_precedence_over_send_default_pii(
    sentry_init, client, capture_items
):
    # ``data_collection`` is the single source of truth: even with
    # ``send_default_pii=False``, the configured cookie behaviour still applies.
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=False,
        _experiments={"data_collection": {"cookies": {"mode": "denylist"}}},
    )
    items = capture_items("event")
    werkzeug_set_cookie(client, "localhost", "sessionid", "123")
    werkzeug_set_cookie(client, "localhost", "csrftoken", "456")
    werkzeug_set_cookie(client, "localhost", "foo", "bar")
    client.get(reverse("view_exc"))

    (event,) = (item.payload for item in items if item.type == "event")
    assert event["request"]["cookies"] == {
        "sessionid": "[Filtered]",
        "csrftoken": "[Filtered]",
        "foo": "bar",
    }

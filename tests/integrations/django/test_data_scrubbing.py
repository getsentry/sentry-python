import pytest
from werkzeug.test import Client

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import DjangoIntegration
from tests.conftest import unpack_werkzeug_response, werkzeug_set_cookie
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
        trace_lifecycle="stream" if span_streaming else "static",
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
        trace_lifecycle="stream" if span_streaming else "static",
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
        trace_lifecycle="stream" if span_streaming else "static",
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
            None,
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
    if expected_cookies is None:
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


# Query string used across the query-param filtering tests below. ``auth`` is a
# built-in sensitive term, so it is redacted by the default denylist.
QUERY_STRING = "toy=tennisball&color=red&auth=secret"


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize(
    "init_kwargs, expected_query_string",
    [
        pytest.param(
            {"send_default_pii": True},
            "toy=tennisball&color=red&auth=secret",
            id="legacy_send_default_pii_true",
        ),
        pytest.param(
            {"send_default_pii": False},
            "toy=tennisball&color=red&auth=secret",
            id="legacy_send_default_pii_false",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            None,
            id="data_collection_off",
        ),
    ],
)
def test_query_string_data_collection(
    sentry_init,
    client,
    capture_events,
    init_kwargs,
    expected_query_string,
):
    sentry_init(integrations=[DjangoIntegration()], **init_kwargs)
    events = capture_events()

    client.get(reverse("view_exc") + "?" + QUERY_STRING)

    (event,) = events

    if expected_query_string is None:
        assert "query_string" not in event["request"]
    else:
        assert event["request"]["query_string"] == expected_query_string


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize(
    "init_kwargs, expected_query",
    [
        pytest.param(
            {"send_default_pii": True},
            "toy=tennisball&color=red&auth=secret",
            id="legacy_send_default_pii_true",
        ),
        pytest.param(
            {"send_default_pii": False},
            None,
            id="legacy_send_default_pii_false",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "url_query_params": {"mode": "allowlist", "terms": ["toy"]}
                    }
                }
            },
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"url_query_params": {"mode": "off"}}
                }
            },
            None,
            id="data_collection_off",
        ),
    ],
)
def test_span_http_query_data_collection(
    sentry_init,
    client,
    capture_items,
    init_kwargs,
    expected_query,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            **init_kwargs.pop("_experiments", {}),
        },
        **init_kwargs,
    )

    items = capture_items("span")

    unpack_werkzeug_response(client.get(reverse("message") + "?" + QUERY_STRING))

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    (root_span,) = (span for span in spans if span["name"] == "/message")

    if expected_query is None:
        assert SPANDATA.HTTP_QUERY not in root_span["attributes"]
    else:
        assert root_span["attributes"][SPANDATA.HTTP_QUERY] == expected_query


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_query_string_empty_legacy_emits_empty_string(
    sentry_init, client, capture_events
):
    sentry_init(integrations=[DjangoIntegration()], send_default_pii=True)
    events = capture_events()

    client.get(reverse("view_exc"))

    (event,) = events
    assert event["request"]["query_string"] == ""


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_empty_query_string_is_dropped_with_data_collection(
    sentry_init, client, capture_events
):
    # ``data_collection`` path: an empty query string is dropped entirely to
    # reduce envelope size, so the ``query_string`` key is absent.
    sentry_init(
        integrations=[DjangoIntegration()],
        _experiments={"data_collection": {}},
    )
    events = capture_events()

    client.get(reverse("view_exc"))

    (event,) = events
    assert "query_string" not in event["request"]

import pytest
from werkzeug.test import Client

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import DjangoIntegration
from tests.conftest import unpack_werkzeug_response, werkzeug_set_cookie
from tests.integrations.django.myapp.wsgi import application
from tests.integrations.django.utils import pytest_mark_django_db_decorator
from tests.integrations.utils import DATA_COLLECTION_USER_INFO_CASES

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse


@pytest.fixture
def client():
    return Client(application)


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
        data_collection=data_collection,
    )
    items = capture_items("event")
    for name, value in cookies_to_set.items():
        werkzeug_set_cookie(client, "localhost", name, value)
    client.get(reverse("view_exc"))

    (event,) = (item.payload for item in items)
    if expected_cookies is None:
        assert "cookies" not in event["request"]
    else:
        assert event["request"]["cookies"] == expected_cookies


# Query string used across the query-param filtering tests below. ``auth`` is a
# built-in sensitive term, so it is redacted by the default denylist.
QUERY_STRING = "toy=tennisball&color=red&auth=secret"


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize(
    "data_collection, expected_query_string",
    [
        pytest.param(
            {},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {"url_query_params": {"mode": "allowlist", "terms": ["toy"]}},
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {"url_query_params": {"mode": "off"}},
            None,
            id="data_collection_off",
        ),
    ],
)
def test_query_string_data_collection(
    sentry_init,
    client,
    capture_events,
    data_collection,
    expected_query_string,
):
    sentry_init(integrations=[DjangoIntegration()], data_collection=data_collection)
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
    "data_collection, expected_query",
    [
        pytest.param(
            {},
            "toy=tennisball&color=red&auth=%5BFiltered%5D",
            id="data_collection_denylist_default",
        ),
        pytest.param(
            {"url_query_params": {"mode": "allowlist", "terms": ["toy"]}},
            "toy=tennisball&color=%5BFiltered%5D&auth=%5BFiltered%5D",
            id="data_collection_allowlist",
        ),
        pytest.param(
            {"url_query_params": {"mode": "off"}},
            None,
            id="data_collection_off",
        ),
    ],
)
def test_span_http_query_data_collection(
    sentry_init,
    client,
    capture_items,
    data_collection,
    expected_query,
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        data_collection=data_collection,
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
def test_empty_query_string_is_dropped_with_data_collection(
    sentry_init, client, capture_events
):
    # ``data_collection`` path: an empty query string is dropped entirely to
    # reduce envelope size, so the ``query_string`` key is absent.
    sentry_init(
        integrations=[DjangoIntegration()],
    )
    events = capture_events()

    client.get(reverse("view_exc"))

    (event,) = events
    assert "query_string" not in event["request"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize("data_collection, expect_ip", DATA_COLLECTION_USER_INFO_CASES)
def test_user_info_span_attributes_data_collection(
    sentry_init, client, capture_items, data_collection, expect_ip
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        data_collection=data_collection,
    )

    items = capture_items("span")

    unpack_werkzeug_response(
        client.get(reverse("message"), environ_base={"REMOTE_ADDR": "127.0.0.1"})
    )

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    root_span = spans[-1]

    if expect_ip:
        assert root_span["attributes"][SPANDATA.USER_IP_ADDRESS] == "127.0.0.1"
        assert root_span["attributes"]["client.address"] == "127.0.0.1"
    else:
        assert SPANDATA.USER_IP_ADDRESS not in root_span["attributes"]
        assert "client.address" not in root_span["attributes"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize(
    "data_collection, expect_user", DATA_COLLECTION_USER_INFO_CASES
)
def test_user_identity_span_attributes_data_collection(
    sentry_init, client, capture_items, data_collection, expect_user
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        data_collection=data_collection,
    )

    unpack_werkzeug_response(client.get(reverse("mylogin")))

    items = capture_items("span")
    unpack_werkzeug_response(client.get(reverse("template_test")))
    sentry_sdk.flush()

    spans = [item.payload for item in items]
    (span,) = (s for s in spans if s["name"] == "/template-test")

    if expect_user:
        assert span["attributes"][SPANDATA.USER_ID] == "1"
        assert span["attributes"][SPANDATA.USER_EMAIL] == "lennon@thebeatles.com"
        assert span["attributes"][SPANDATA.USER_NAME] == "john"
    else:
        assert SPANDATA.USER_ID not in span["attributes"]
        assert SPANDATA.USER_EMAIL not in span["attributes"]
        assert SPANDATA.USER_NAME not in span["attributes"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize("data_collection, expect_ip", DATA_COLLECTION_USER_INFO_CASES)
def test_user_info_error_event_data_collection(
    sentry_init, client, capture_events, data_collection, expect_ip
):
    sentry_init(integrations=[DjangoIntegration()], data_collection=data_collection)
    events = capture_events()

    client.get(reverse("view_exc"), environ_base={"REMOTE_ADDR": "127.0.0.1"})

    (event,) = events

    if expect_ip:
        assert event["user"]["ip_address"] == "127.0.0.1"
        assert event["request"]["env"]["REMOTE_ADDR"] == "127.0.0.1"
    else:
        assert "ip_address" not in event.get("user", {})
        assert "REMOTE_ADDR" not in event["request"]["env"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.parametrize(
    "data_collection, expect_user", DATA_COLLECTION_USER_INFO_CASES
)
def test_user_identity_error_event_data_collection(
    sentry_init, client, capture_events, data_collection, expect_user
):
    sentry_init(integrations=[DjangoIntegration()], data_collection=data_collection)
    events = capture_events()

    client.get(reverse("mylogin"))
    client.get(reverse("view_exc"))

    event = events[-1]

    if expect_user:
        assert event["user"]["id"] == "1"
        assert event["user"]["email"] == "lennon@thebeatles.com"
        assert event["user"]["username"] == "john"
    else:
        assert "id" not in event.get("user", {})
        assert "email" not in event.get("user", {})
        assert "username" not in event.get("user", {})


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_error_event_no_user_ip_address_without_remote_addr(
    sentry_init, client, capture_events
):
    sentry_init(
        integrations=[DjangoIntegration()],
        data_collection={"user_info": True},
    )
    events = capture_events()

    client.get(reverse("view_exc"))

    (event,) = events

    assert "ip_address" not in event.get("user", {})

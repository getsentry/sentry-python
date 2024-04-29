import pytest
import random

from django import VERSION as DJANGO_VERSION

from werkzeug.test import Client

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.django.caching import _get_span_description
from tests.integrations.django.myapp.wsgi import application
from tests.integrations.django.utils import pytest_mark_django_db_decorator


DJANGO_VERSION = DJANGO_VERSION[:2]


@pytest.fixture
def client():
    return Client(application)


@pytest.fixture
def use_django_caching(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake-%s" % random.randint(1, 1000000),
        }
    }


@pytest.fixture
def use_django_caching_with_middlewares(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake-%s" % random.randint(1, 1000000),
        }
    }
    if hasattr(settings, "MIDDLEWARE"):
        middleware = settings.MIDDLEWARE
    elif hasattr(settings, "MIDDLEWARE_CLASSES"):
        middleware = settings.MIDDLEWARE_CLASSES
    else:
        middleware = None

    if middleware is not None:
        middleware.insert(0, "django.middleware.cache.UpdateCacheMiddleware")
        middleware.append("django.middleware.cache.FetchFromCacheMiddleware")


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="Requires Django >= 1.9")
def test_cache_spans_disabled_middleware(
    sentry_init, client, capture_events, use_django_caching_with_middlewares
):
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

    client.get(reverse("not_cached_view"))
    client.get(reverse("not_cached_view"))

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 0
    assert len(second_event["spans"]) == 0


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="Requires Django >= 1.9")
def test_cache_spans_disabled_decorator(
    sentry_init, client, capture_events, use_django_caching
):
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

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 0
    assert len(second_event["spans"]) == 0


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="Requires Django >= 1.9")
def test_cache_spans_disabled_templatetag(
    sentry_init, client, capture_events, use_django_caching
):
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

    client.get(reverse("view_with_cached_template_fragment"))
    client.get(reverse("view_with_cached_template_fragment"))

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 0
    assert len(second_event["spans"]) == 0


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="Requires Django >= 1.9")
def test_cache_spans_middleware(
    sentry_init, client, capture_events, use_django_caching_with_middlewares
):
    sentry_init(
        integrations=[
            DjangoIntegration(
                cache_spans=True,
                middleware_spans=False,
                signals_spans=False,
            )
        ],
        traces_sample_rate=1.0,
    )

    client.application.load_middleware()
    events = capture_events()

    client.get(reverse("not_cached_view"))
    client.get(reverse("not_cached_view"))

    (first_event, second_event) = events
    # first_event - cache.get
    assert first_event["spans"][0]["op"] == "cache.get"
    assert first_event["spans"][0]["description"].startswith(
        "get views.decorators.cache.cache_header."
    )
    assert first_event["spans"][0]["data"]["network.peer.address"] is not None
    assert first_event["spans"][0]["data"]["cache.key"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]
    # first_event - cache.set
    assert first_event["spans"][1]["op"] == "cache.set"
    assert first_event["spans"][1]["description"].startswith(
        "set views.decorators.cache.cache_header."
    )
    assert first_event["spans"][1]["data"]["network.peer.address"] is not None
    assert first_event["spans"][1]["data"]["cache.key"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert "cache.item_size" not in first_event["spans"][1]["data"]
    # second_event - cache.get
    assert second_event["spans"][0]["op"] == "cache.get"
    assert second_event["spans"][0]["description"].startswith(
        "get views.decorators.cache.cache_header."
    )
    assert second_event["spans"][0]["data"]["network.peer.address"] is not None
    assert second_event["spans"][0]["data"]["cache.key"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert not second_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in second_event["spans"][0]["data"]
    # second_event - cache.get 2
    assert second_event["spans"][1]["op"] == "cache.get"
    assert second_event["spans"][1]["description"].startswith(
        "get views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["network.peer.address"] is not None
    assert second_event["spans"][1]["data"]["cache.key"].startswith(
        "views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert "cache.item_size" not in second_event["spans"][1]["data"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="Requires Django >= 1.9")
def test_cache_spans_decorator(sentry_init, client, capture_events, use_django_caching):
    sentry_init(
        integrations=[
            DjangoIntegration(
                cache_spans=True,
                middleware_spans=False,
                signals_spans=False,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    (first_event, second_event) = events
    # first_event - cache.get
    assert first_event["spans"][0]["op"] == "cache.get"
    assert first_event["spans"][0]["description"].startswith(
        "get views.decorators.cache.cache_header."
    )
    assert first_event["spans"][0]["data"]["network.peer.address"] is not None
    assert first_event["spans"][0]["data"]["cache.key"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]
    # first_event - cache.set
    assert first_event["spans"][1]["op"] == "cache.set"
    assert first_event["spans"][1]["description"].startswith(
        "set views.decorators.cache.cache_header."
    )
    assert first_event["spans"][1]["data"]["network.peer.address"] is not None
    assert first_event["spans"][1]["data"]["cache.key"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert "cache.item_size" not in first_event["spans"][1]["data"]
    # second_event - cache.get
    assert second_event["spans"][1]["op"] == "cache.get"
    assert second_event["spans"][1]["description"].startswith(
        "get views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["network.peer.address"] is not None
    assert second_event["spans"][1]["data"]["cache.key"].startswith(
        "views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert "cache.item_size" not in second_event["spans"][0]["data"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.skipif(DJANGO_VERSION < (1, 9), reason="Requires Django >= 1.9")
def test_cache_spans_templatetag(
    sentry_init, client, capture_events, use_django_caching
):
    sentry_init(
        integrations=[
            DjangoIntegration(
                cache_spans=True,
                middleware_spans=False,
                signals_spans=False,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("view_with_cached_template_fragment"))
    client.get(reverse("view_with_cached_template_fragment"))

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 2
    # first_event - cache.get
    assert first_event["spans"][0]["op"] == "cache.get"
    assert first_event["spans"][0]["description"].startswith(
        "get template.cache.some_identifier."
    )
    assert first_event["spans"][0]["data"]["network.peer.address"] is not None
    assert first_event["spans"][0]["data"]["cache.key"].startswith(
        "template.cache.some_identifier."
    )
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]
    # first_event - cache.set
    assert first_event["spans"][1]["op"] == "cache.set"
    assert first_event["spans"][1]["description"].startswith(
        "set template.cache.some_identifier."
    )
    assert first_event["spans"][1]["data"]["network.peer.address"] is not None
    assert first_event["spans"][1]["data"]["cache.key"].startswith(
        "template.cache.some_identifier."
    )
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert "cache.item_size" not in first_event["spans"][1]["data"]
    # second_event - cache.get
    assert second_event["spans"][0]["op"] == "cache.get"
    assert second_event["spans"][0]["description"].startswith(
        "get template.cache.some_identifier."
    )
    assert second_event["spans"][0]["data"]["network.peer.address"] is not None
    assert second_event["spans"][0]["data"]["cache.key"].startswith(
        "template.cache.some_identifier."
    )
    assert second_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in second_event["spans"][0]["data"]


@pytest.mark.parametrize(
    "method_name, args, kwargs, expected_description",
    [
        ("get", None, None, "get "),
        ("get", [], {}, "get "),
        ("get", ["bla", "blub", "foo"], {}, "get bla"),
        (
            "get_many",
            [["bla 1", "bla 2", "bla 3"], "blub", "foo"],
            {},
            "get_many ['bla 1', 'bla 2', 'bla 3']",
        ),
        (
            "get_many",
            [["bla 1", "bla 2", "bla 3"], "blub", "foo"],
            {"key": "bar"},
            "get_many ['bla 1', 'bla 2', 'bla 3']",
        ),
        ("get", [], {"key": "bar"}, "get bar"),
        (
            "get",
            "something",
            {},
            "get s",
        ),  # this should never happen, just making sure that we are not raising an exception in that case.
    ],
)
def test_cache_spans_get_span_description(
    method_name, args, kwargs, expected_description
):
    assert _get_span_description(method_name, args, kwargs) == expected_description

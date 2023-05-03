import pytest
import random

from werkzeug.test import Client

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.myapp.wsgi import application

from sentry_sdk._compat import PY2


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
    settings.MIDDLEWARE.insert(0, "django.middleware.cache.UpdateCacheMiddleware")
    settings.MIDDLEWARE.append("django.middleware.cache.FetchFromCacheMiddleware")


@pytest.mark.forked
def test_cache_spans_disabled_middleware(
    sentry_init,
    client,
    capture_events,
    use_django_caching_with_middlewares,
    settings,
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

    # Calling client.get() messes up the Django urlpatterns,
    # so we reverse all urls, before we get them with client.get()
    url = reverse("not_cached_view")
    client.get(url)
    client.get(url)

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 0
    assert len(second_event["spans"]) == 0


@pytest.mark.forked
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

    # Calling client.get() messes up the Django urlpatterns,
    # so we reverse all urls, before we get them with client.get()
    url = reverse("cached_view")
    client.get(url)
    client.get(url)

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 0
    assert len(second_event["spans"]) == 0


@pytest.mark.forked
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

    # Calling client.get() messes up the Django urlpatterns,
    # so we reverse all urls, before we get them with client.get()
    url = reverse("view_with_cached_template_fragment")
    client.get(url)
    client.get(url)

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 0
    assert len(second_event["spans"]) == 0


@pytest.mark.forked
def test_cache_spans_middleware(
    sentry_init,
    client,
    capture_events,
    use_django_caching_with_middlewares,
    settings,
):
    client.application.load_middleware()

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

    # Calling client.get() messes up the Django urlpatterns,
    # so we reverse all urls, before we get them with client.get()
    url = reverse("not_cached_view")
    client.get(url)
    client.get(url)

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 1
    assert first_event["spans"][0]["op"] == "cache"
    assert first_event["spans"][0]["description"].startswith(
        "get views.decorators.cache.cache_header."
    )
    assert first_event["spans"][0]["data"] == {"cache.hit": False}

    assert len(second_event["spans"]) == 2
    assert second_event["spans"][0]["op"] == "cache"
    assert second_event["spans"][0]["description"].startswith(
        "get views.decorators.cache.cache_header."
    )
    assert second_event["spans"][0]["data"] == {"cache.hit": False}

    assert second_event["spans"][1]["op"] == "cache"
    assert second_event["spans"][1]["description"].startswith(
        "get views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"] == {
        "cache.hit": True,
        "cache.item_size": 112 if PY2 else 58,
    }


@pytest.mark.forked
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

    # Calling client.get() messes up the Django urlpatterns,
    # so we reverse all urls, before we get them with client.get()
    url = reverse("cached_view")
    client.get(url)
    client.get(url)

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 1
    assert first_event["spans"][0]["op"] == "cache"
    assert first_event["spans"][0]["description"].startswith(
        "get views.decorators.cache.cache_header."
    )
    assert first_event["spans"][0]["data"] == {"cache.hit": False}

    assert len(second_event["spans"]) == 2
    assert second_event["spans"][0]["op"] == "cache"
    assert second_event["spans"][0]["description"].startswith(
        "get views.decorators.cache.cache_header."
    )
    assert second_event["spans"][0]["data"] == {"cache.hit": False}

    assert second_event["spans"][1]["op"] == "cache"
    assert second_event["spans"][1]["description"].startswith(
        "get views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"] == {
        "cache.hit": True,
        "cache.item_size": 111 if PY2 else 58,
    }


@pytest.mark.forked
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

    # Calling client.get() messes up the Django urlpatterns,
    # so we reverse all urls, before we get them with client.get()
    url = reverse("view_with_cached_template_fragment")
    client.get(url)
    client.get(url)

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 1
    assert first_event["spans"][0]["op"] == "cache"
    assert first_event["spans"][0]["description"].startswith(
        "get template.cache.some_identifier."
    )
    assert first_event["spans"][0]["data"] == {"cache.hit": False}

    assert len(second_event["spans"]) == 1
    assert second_event["spans"][0]["op"] == "cache"
    assert second_event["spans"][0]["description"].startswith(
        "get template.cache.some_identifier."
    )
    assert second_event["spans"][0]["data"] == {
        "cache.hit": True,
        "cache.item_size": 51,
    }

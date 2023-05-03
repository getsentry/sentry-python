import pytest

from sentry_sdk.integrations.django import DjangoIntegration
from tests.integrations.django.utils import pytest_mark_django_db_decorator

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_disabled_middleware_XXX(
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

    client.get(reverse("not_cached_view"))
    client.get(reverse("not_cached_view"))

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 0
    assert len(second_event["spans"]) == 0


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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

    client.get(reverse("not_cached_view"))
    client.get(reverse("not_cached_view"))

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
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert "cache.item_size" in second_event["spans"][1]["data"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert "cache.item_size" in second_event["spans"][1]["data"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
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
    assert second_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" in second_event["spans"][0]["data"]

import os
import random
import uuid

import pytest
from django import VERSION as DJANGO_VERSION
from werkzeug.test import Client

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

import sentry_sdk
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


@pytest.fixture
def use_django_caching_with_port(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
            "LOCATION": "redis://username:password@127.0.0.1:6379",
        }
    }


@pytest.fixture
def use_django_caching_without_port(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
            "LOCATION": "redis://example.com",
        }
    }


@pytest.fixture
def use_django_caching_with_cluster(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
            "LOCATION": [
                "redis://127.0.0.1:6379",
                "redis://127.0.0.2:6378",
                "redis://127.0.0.3:6377",
            ],
        }
    }


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
        "views.decorators.cache.cache_header."
    )
    assert first_event["spans"][0]["data"]["network.peer.address"] is not None
    assert first_event["spans"][0]["data"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]
    # first_event - cache.put
    assert first_event["spans"][1]["op"] == "cache.put"
    assert first_event["spans"][1]["description"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert first_event["spans"][1]["data"]["network.peer.address"] is not None
    assert first_event["spans"][1]["data"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert first_event["spans"][1]["data"]["cache.item_size"] == 2
    # second_event - cache.get
    assert second_event["spans"][0]["op"] == "cache.get"
    assert second_event["spans"][0]["description"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert second_event["spans"][0]["data"]["network.peer.address"] is not None
    assert second_event["spans"][0]["data"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert second_event["spans"][0]["data"]["cache.hit"]
    assert second_event["spans"][0]["data"]["cache.item_size"] == 2
    # second_event - cache.get 2
    assert second_event["spans"][1]["op"] == "cache.get"
    assert second_event["spans"][1]["description"].startswith(
        "views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["network.peer.address"] is not None
    assert second_event["spans"][1]["data"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert second_event["spans"][1]["data"]["cache.item_size"] == 58


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
        "views.decorators.cache.cache_header."
    )
    assert first_event["spans"][0]["data"]["network.peer.address"] is not None
    assert first_event["spans"][0]["data"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]
    # first_event - cache.put
    assert first_event["spans"][1]["op"] == "cache.put"
    assert first_event["spans"][1]["description"].startswith(
        "views.decorators.cache.cache_header."
    )
    assert first_event["spans"][1]["data"]["network.peer.address"] is not None
    assert first_event["spans"][1]["data"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert first_event["spans"][1]["data"]["cache.item_size"] == 2
    # second_event - cache.get
    assert second_event["spans"][1]["op"] == "cache.get"
    assert second_event["spans"][1]["description"].startswith(
        "views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["network.peer.address"] is not None
    assert second_event["spans"][1]["data"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_page."
    )
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert second_event["spans"][1]["data"]["cache.item_size"] == 58


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
        "template.cache.some_identifier."
    )
    assert first_event["spans"][0]["data"]["network.peer.address"] is not None
    assert first_event["spans"][0]["data"]["cache.key"][0].startswith(
        "template.cache.some_identifier."
    )
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]
    # first_event - cache.put
    assert first_event["spans"][1]["op"] == "cache.put"
    assert first_event["spans"][1]["description"].startswith(
        "template.cache.some_identifier."
    )
    assert first_event["spans"][1]["data"]["network.peer.address"] is not None
    assert first_event["spans"][1]["data"]["cache.key"][0].startswith(
        "template.cache.some_identifier."
    )
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert first_event["spans"][1]["data"]["cache.item_size"] == 51
    # second_event - cache.get
    assert second_event["spans"][0]["op"] == "cache.get"
    assert second_event["spans"][0]["description"].startswith(
        "template.cache.some_identifier."
    )
    assert second_event["spans"][0]["data"]["network.peer.address"] is not None
    assert second_event["spans"][0]["data"]["cache.key"][0].startswith(
        "template.cache.some_identifier."
    )
    assert second_event["spans"][0]["data"]["cache.hit"]
    assert second_event["spans"][0]["data"]["cache.item_size"] == 51


@pytest.mark.parametrize(
    "method_name, args, kwargs, expected_description",
    [
        (None, None, None, ""),
        ("get", None, None, ""),
        ("get", [], {}, ""),
        ("get", ["bla", "blub", "foo"], {}, "bla"),
        ("get", [uuid.uuid4().bytes], {}, ""),
        (
            "get_many",
            [["bla1", "bla2", "bla3"], "blub", "foo"],
            {},
            "bla1, bla2, bla3",
        ),
        (
            "get_many",
            [["bla:1", "bla:2", "bla:3"], "blub", "foo"],
            {"key": "bar"},
            "bla:1, bla:2, bla:3",
        ),
        ("get", [], {"key": "bar"}, "bar"),
        (
            "get",
            "something",
            {},
            "s",
        ),  # this case should never happen, just making sure that we are not raising an exception in that case.
    ],
)
def test_cache_spans_get_span_description(
    method_name, args, kwargs, expected_description
):
    assert _get_span_description(method_name, args, kwargs) == expected_description


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_location_with_port(
    sentry_init, client, capture_events, use_django_caching_with_port
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

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    for event in events:
        for span in event["spans"]:
            assert (
                span["data"]["network.peer.address"] == "redis://127.0.0.1"
            )  # Note: the username/password are not included in the address
            assert span["data"]["network.peer.port"] == 6379


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_location_without_port(
    sentry_init, client, capture_events, use_django_caching_without_port
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

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    for event in events:
        for span in event["spans"]:
            assert span["data"]["network.peer.address"] == "redis://example.com"
            assert "network.peer.port" not in span["data"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_location_with_cluster(
    sentry_init, client, capture_events, use_django_caching_with_cluster
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

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    for event in events:
        for span in event["spans"]:
            # because it is a cluster we do not know what host is actually accessed, so we omit the data
            assert "network.peer.address" not in span["data"].keys()
            assert "network.peer.port" not in span["data"].keys()


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_item_size(sentry_init, client, capture_events, use_django_caching):
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
    assert len(first_event["spans"]) == 3
    assert first_event["spans"][0]["op"] == "cache.get"
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]

    assert first_event["spans"][1]["op"] == "cache.put"
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert first_event["spans"][1]["data"]["cache.item_size"] == 2

    assert first_event["spans"][2]["op"] == "cache.put"
    assert "cache.hit" not in first_event["spans"][2]["data"]
    assert first_event["spans"][2]["data"]["cache.item_size"] == 58

    assert len(second_event["spans"]) == 2
    assert second_event["spans"][0]["op"] == "cache.get"
    assert second_event["spans"][0]["data"]["cache.hit"]
    assert second_event["spans"][0]["data"]["cache.item_size"] == 2

    assert second_event["spans"][1]["op"] == "cache.get"
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert second_event["spans"][1]["data"]["cache.item_size"] == 58


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_get_custom_default(
    sentry_init, capture_events, use_django_caching
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

    id = os.getpid()

    from django.core.cache import cache

    with sentry_sdk.start_transaction():
        cache.set(f"S{id}", "Sensitive1")
        cache.set(f"S{id + 1}", "")

        cache.get(f"S{id}", "null")
        cache.get(f"S{id}", default="null")

        cache.get(f"S{id + 1}", "null")
        cache.get(f"S{id + 1}", default="null")

        cache.get(f"S{id + 2}", "null")
        cache.get(f"S{id + 2}", default="null")

    (transaction,) = events
    assert len(transaction["spans"]) == 8

    assert transaction["spans"][0]["op"] == "cache.put"
    assert transaction["spans"][0]["description"] == f"S{id}"

    assert transaction["spans"][1]["op"] == "cache.put"
    assert transaction["spans"][1]["description"] == f"S{id + 1}"

    for span in (transaction["spans"][2], transaction["spans"][3]):
        assert span["op"] == "cache.get"
        assert span["description"] == f"S{id}"
        assert span["data"]["cache.hit"]
        assert span["data"]["cache.item_size"] == 10

    for span in (transaction["spans"][4], transaction["spans"][5]):
        assert span["op"] == "cache.get"
        assert span["description"] == f"S{id + 1}"
        assert span["data"]["cache.hit"]
        assert span["data"]["cache.item_size"] == 0

    for span in (transaction["spans"][6], transaction["spans"][7]):
        assert span["op"] == "cache.get"
        assert span["description"] == f"S{id + 2}"
        assert not span["data"]["cache.hit"]
        assert "cache.item_size" not in span["data"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_get_many(sentry_init, capture_events, use_django_caching):
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

    id = os.getpid()

    from django.core.cache import cache

    with sentry_sdk.start_transaction():
        cache.get_many([f"S{id}", f"S{id + 1}"])
        cache.set(f"S{id}", "Sensitive1")
        cache.get_many([f"S{id}", f"S{id + 1}"])

    (transaction,) = events
    assert len(transaction["spans"]) == 7

    assert transaction["spans"][0]["op"] == "cache.get"
    assert transaction["spans"][0]["description"] == f"S{id}, S{id + 1}"

    assert transaction["spans"][1]["op"] == "cache.get"
    assert transaction["spans"][1]["description"] == f"S{id}"

    assert transaction["spans"][2]["op"] == "cache.get"
    assert transaction["spans"][2]["description"] == f"S{id + 1}"

    assert transaction["spans"][3]["op"] == "cache.put"
    assert transaction["spans"][3]["description"] == f"S{id}"

    assert transaction["spans"][4]["op"] == "cache.get"
    assert transaction["spans"][4]["description"] == f"S{id}, S{id + 1}"

    assert transaction["spans"][5]["op"] == "cache.get"
    assert transaction["spans"][5]["description"] == f"S{id}"

    assert transaction["spans"][6]["op"] == "cache.get"
    assert transaction["spans"][6]["description"] == f"S{id + 1}"


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_set_many(sentry_init, capture_events, use_django_caching):
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

    id = os.getpid()

    from django.core.cache import cache

    with sentry_sdk.start_transaction():
        cache.set_many({f"S{id}": "Sensitive1", f"S{id + 1}": "Sensitive2"})
        cache.get(f"S{id}")

    (transaction,) = events
    assert len(transaction["spans"]) == 4

    assert transaction["spans"][0]["op"] == "cache.put"
    assert transaction["spans"][0]["description"] == f"S{id}, S{id + 1}"

    assert transaction["spans"][1]["op"] == "cache.put"
    assert transaction["spans"][1]["description"] == f"S{id}"

    assert transaction["spans"][2]["op"] == "cache.put"
    assert transaction["spans"][2]["description"] == f"S{id + 1}"

    assert transaction["spans"][3]["op"] == "cache.get"
    assert transaction["spans"][3]["description"] == f"S{id}"


@pytest.mark.forked
@pytest_mark_django_db_decorator()
@pytest.mark.skipif(DJANGO_VERSION <= (1, 11), reason="Requires Django > 1.11")
def test_span_origin_cache(sentry_init, client, capture_events, use_django_caching):
    sentry_init(
        integrations=[
            DjangoIntegration(
                middleware_spans=True,
                signals_spans=True,
                cache_spans=True,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("cached_view"))

    (transaction,) = events

    assert transaction["contexts"]["trace"]["origin"] == "auto.http.django"

    cache_span_found = False
    for span in transaction["spans"]:
        assert span["origin"] == "auto.http.django"
        if span["op"].startswith("cache."):
            cache_span_found = True

    assert cache_span_found

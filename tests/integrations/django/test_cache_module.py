import pytest
import os
import random

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
    assert first_event["spans"][0]["op"] == "cache.get_item"
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
    assert first_event["spans"][1]["op"] == "cache.set_item"
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
    assert second_event["spans"][0]["op"] == "cache.get_item"
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
    assert second_event["spans"][1]["op"] == "cache.get_item"
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
    assert first_event["spans"][0]["op"] == "cache.get_item"
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
    assert first_event["spans"][1]["op"] == "cache.set_item"
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
    assert second_event["spans"][1]["op"] == "cache.get_item"
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
    assert first_event["spans"][0]["op"] == "cache.get_item"
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
    assert first_event["spans"][1]["op"] == "cache.set_item"
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
    assert second_event["spans"][0]["op"] == "cache.get_item"
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
                cache_spans_add_item_size=True,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    (first_event, second_event) = events
    assert len(first_event["spans"]) == 3
    assert first_event["spans"][0]["op"] == "cache.get_item"
    assert not first_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in first_event["spans"][0]["data"]

    assert first_event["spans"][1]["op"] == "cache.set_item"
    assert "cache.hit" not in first_event["spans"][1]["data"]
    assert first_event["spans"][1]["data"]["cache.item_size"] == 2

    assert first_event["spans"][2]["op"] == "cache.set_item"
    assert "cache.hit" not in first_event["spans"][2]["data"]
    assert first_event["spans"][2]["data"]["cache.item_size"] == 58

    assert len(second_event["spans"]) == 2
    assert second_event["spans"][0]["op"] == "cache.get_item"
    assert not second_event["spans"][0]["data"]["cache.hit"]
    assert "cache.item_size" not in second_event["spans"][0]["data"]

    assert second_event["spans"][1]["op"] == "cache.get_item"
    assert second_event["spans"][1]["data"]["cache.hit"]
    assert second_event["spans"][1]["data"]["cache.item_size"] == 58


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_get_many(sentry_init, capture_events, use_django_caching):
    sentry_init(
        integrations=[
            DjangoIntegration(
                cache_spans=True,
                middleware_spans=False,
                signals_spans=False,
                cache_spans_add_item_size=True,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    id = os.getpid()

    from django.core.cache import cache

    with sentry_sdk.start_transaction():
        cache.get_many([f"S{id}", f"S{id+1}"])
        cache.set(f"S{id}", "Sensitive1")
        cache.get_many([f"S{id}", f"S{id+1}"])

    (transaction,) = events
    assert len(transaction["spans"]) == 7

    assert transaction["spans"][0]["op"] == "cache.get_item"
    assert transaction["spans"][0]["description"] == f"get_many ['S{id}', 'S{id+1}']"

    assert transaction["spans"][1]["op"] == "cache.get_item"
    assert transaction["spans"][1]["description"] == f"get S{id}"

    assert transaction["spans"][2]["op"] == "cache.get_item"
    assert transaction["spans"][2]["description"] == f"get S{id+1}"

    assert transaction["spans"][3]["op"] == "cache.set_item"
    assert transaction["spans"][3]["description"] == f"set S{id}"

    assert transaction["spans"][4]["op"] == "cache.get_item"
    assert transaction["spans"][4]["description"] == f"get_many ['S{id}', 'S{id+1}']"

    assert transaction["spans"][5]["op"] == "cache.get_item"
    assert transaction["spans"][5]["description"] == f"get S{id}"

    assert transaction["spans"][6]["op"] == "cache.get_item"
    assert transaction["spans"][6]["description"] == f"get S{id+1}"


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_set_many(sentry_init, capture_events, use_django_caching):
    sentry_init(
        integrations=[
            DjangoIntegration(
                cache_spans=True,
                middleware_spans=False,
                signals_spans=False,
                cache_spans_add_item_size=True,
            )
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    id = os.getpid()

    from django.core.cache import cache

    with sentry_sdk.start_transaction():
        cache.set_many({f"S{id}": "Sensitive1", f"S{id+1}": "Sensitive2"})
        cache.get(f"S{id}")

    (transaction,) = events
    assert len(transaction["spans"]) == 4

    assert transaction["spans"][0]["op"] == "cache.set_item"
    assert (
        transaction["spans"][0]["description"]
        == f"set_many {{'S{id}': '[Filtered]', 'S{id+1}': '[Filtered]'}}"
    )

    assert transaction["spans"][1]["op"] == "cache.set_item"
    assert transaction["spans"][1]["description"] == f"set S{id}"

    assert transaction["spans"][2]["op"] == "cache.set_item"
    assert transaction["spans"][2]["description"] == f"set S{id+1}"

    assert transaction["spans"][3]["op"] == "cache.get_item"
    assert transaction["spans"][3]["description"] == f"get S{id}"

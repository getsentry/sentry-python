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
def test_cache_spans_disabled_middleware(
    sentry_init,
    client,
    capture_items,
    use_django_caching_with_middlewares,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("not_cached_view"))
    client.get(reverse("not_cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_disabled_decorator(
    sentry_init,
    client,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_disabled_templatetag(
    sentry_init,
    client,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("view_with_cached_template_fragment"))
    client.get(reverse("view_with_cached_template_fragment"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_middleware(
    sentry_init,
    client,
    capture_items,
    use_django_caching_with_middlewares,
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
        trace_lifecycle="stream",
    )

    client.application.load_middleware()
    items = capture_items("span")

    client.get(reverse("not_cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    # first_event - cache.get
    assert spans[0]["attributes"]["sentry.op"] == "cache.get"
    assert spans[0]["name"].startswith("views.decorators.cache.cache_header.")
    assert spans[0]["attributes"]["network.peer.address"] is not None
    assert spans[0]["attributes"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert not spans[0]["attributes"]["cache.hit"]
    assert "cache.item_size" not in spans[0]["attributes"]
    # first_event - cache.put
    assert spans[1]["attributes"]["sentry.op"] == "cache.put"
    assert spans[1]["name"].startswith("views.decorators.cache.cache_header.")
    assert spans[1]["attributes"]["network.peer.address"] is not None
    assert spans[1]["attributes"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert "cache.hit" not in spans[1]["attributes"]
    assert spans[1]["attributes"]["cache.item_size"] == 2

    client.get(reverse("not_cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    # second_event - cache.get
    assert spans[4]["attributes"]["sentry.op"] == "cache.get"
    assert spans[4]["name"].startswith("views.decorators.cache.cache_header.")
    assert spans[4]["attributes"]["network.peer.address"] is not None
    assert spans[4]["attributes"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert spans[4]["attributes"]["cache.hit"]
    assert spans[4]["attributes"]["cache.item_size"] == 2
    # second_event - cache.get 2
    assert spans[5]["attributes"]["sentry.op"] == "cache.get"
    assert spans[5]["name"].startswith("views.decorators.cache.cache_page.")
    assert spans[5]["attributes"]["network.peer.address"] is not None
    assert spans[5]["attributes"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_page."
    )
    assert spans[5]["attributes"]["cache.hit"]
    assert spans[5]["attributes"]["cache.item_size"] == 58


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_decorator(
    sentry_init,
    client,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    # first_event - cache.get
    assert spans[0]["attributes"]["sentry.op"] == "cache.get"
    assert spans[0]["name"].startswith("views.decorators.cache.cache_header.")
    assert spans[0]["attributes"]["network.peer.address"] is not None
    assert spans[0]["attributes"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert not spans[0]["attributes"]["cache.hit"]
    assert "cache.item_size" not in spans[0]["attributes"]
    # first_event - cache.put
    assert spans[1]["attributes"]["sentry.op"] == "cache.put"
    assert spans[1]["name"].startswith("views.decorators.cache.cache_header.")
    assert spans[1]["attributes"]["network.peer.address"] is not None
    assert spans[1]["attributes"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_header."
    )
    assert "cache.hit" not in spans[1]["attributes"]
    assert spans[1]["attributes"]["cache.item_size"] == 2
    # second_event - cache.get
    assert spans[5]["attributes"]["sentry.op"] == "cache.get"
    assert spans[5]["name"].startswith("views.decorators.cache.cache_page.")
    assert spans[5]["attributes"]["network.peer.address"] is not None
    assert spans[5]["attributes"]["cache.key"][0].startswith(
        "views.decorators.cache.cache_page."
    )
    assert spans[5]["attributes"]["cache.hit"]
    assert spans[5]["attributes"]["cache.item_size"] == 58


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_templatetag(
    sentry_init,
    client,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("view_with_cached_template_fragment"))
    client.get(reverse("view_with_cached_template_fragment"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 5
    # first_event - cache.get
    assert spans[0]["attributes"]["sentry.op"] == "cache.get"
    assert spans[0]["name"].startswith("template.cache.some_identifier.")
    assert spans[0]["attributes"]["network.peer.address"] is not None
    assert spans[0]["attributes"]["cache.key"][0].startswith(
        "template.cache.some_identifier."
    )
    assert not spans[0]["attributes"]["cache.hit"]
    assert "cache.item_size" not in spans[0]["attributes"]
    # first_event - cache.put
    assert spans[1]["attributes"]["sentry.op"] == "cache.put"
    assert spans[1]["name"].startswith("template.cache.some_identifier.")
    assert spans[1]["attributes"]["network.peer.address"] is not None
    assert spans[1]["attributes"]["cache.key"][0].startswith(
        "template.cache.some_identifier."
    )
    assert "cache.hit" not in spans[1]["attributes"]
    assert spans[1]["attributes"]["cache.item_size"] == 51
    # second_event - cache.get
    assert spans[3]["attributes"]["sentry.op"] == "cache.get"
    assert spans[3]["name"].startswith("template.cache.some_identifier.")
    assert spans[3]["attributes"]["network.peer.address"] is not None
    assert spans[3]["attributes"]["cache.key"][0].startswith(
        "template.cache.some_identifier."
    )
    assert spans[3]["attributes"]["cache.hit"]
    assert spans[3]["attributes"]["cache.item_size"] == 51


@pytest.mark.parametrize(
    "method_name, args, kwargs, expected_name",
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
def test_cache_spans_get_span_name(method_name, args, kwargs, expected_name):
    assert _get_span_description(method_name, args, kwargs) == expected_name


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_location_with_port(
    sentry_init,
    client,
    capture_items,
    use_django_caching_with_port,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    for span in spans:
        if span["is_segment"] is True:
            continue

        assert (
            span["attributes"]["network.peer.address"] == "redis://127.0.0.1"
        )  # Note: the username/password are not included in the address
        assert span["attributes"]["network.peer.port"] == 6379


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_location_without_port(
    sentry_init,
    client,
    capture_items,
    use_django_caching_without_port,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    for span in spans:
        if span["is_segment"] is True:
            continue

        assert span["attributes"]["network.peer.address"] == "redis://example.com"
        assert "network.peer.port" not in span["attributes"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_location_with_cluster(
    sentry_init,
    client,
    capture_items,
    use_django_caching_with_cluster,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    for span in spans:
        # because it is a cluster we do not know what host is actually accessed, so we omit the data
        assert "network.peer.address" not in span["attributes"].keys()
        assert "network.peer.port" not in span["attributes"].keys()


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_item_size(
    sentry_init,
    client,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client.get(reverse("cached_view"))
    client.get(reverse("cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 7
    assert spans[0]["attributes"]["sentry.op"] == "cache.get"
    assert not spans[0]["attributes"]["cache.hit"]
    assert "cache.item_size" not in spans[0]["attributes"]

    assert spans[1]["attributes"]["sentry.op"] == "cache.put"
    assert "cache.hit" not in spans[1]["attributes"]
    assert spans[1]["attributes"]["cache.item_size"] == 2

    assert spans[2]["attributes"]["sentry.op"] == "cache.put"
    assert "cache.hit" not in spans[2]["attributes"]
    assert spans[2]["attributes"]["cache.item_size"] == 58

    assert spans[4]["attributes"]["sentry.op"] == "cache.get"
    assert spans[4]["attributes"]["cache.hit"]
    assert spans[4]["attributes"]["cache.item_size"] == 2

    assert spans[5]["attributes"]["sentry.op"] == "cache.get"
    assert spans[5]["attributes"]["cache.hit"]
    assert spans[5]["attributes"]["cache.item_size"] == 58


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_get_custom_default(
    sentry_init,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )

    id = os.getpid()

    from django.core.cache import cache

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        cache.set(f"S{id}", "Sensitive1")
        cache.set(f"S{id + 1}", "")

        cache.get(f"S{id}", "null")
        cache.get(f"S{id}", default="null")

        cache.get(f"S{id + 1}", "null")
        cache.get(f"S{id + 1}", default="null")

        cache.get(f"S{id + 2}", "null")
        cache.get(f"S{id + 2}", default="null")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 9

    assert spans[0]["attributes"]["sentry.op"] == "cache.put"
    assert spans[0]["name"] == f"S{id}"

    assert spans[1]["attributes"]["sentry.op"] == "cache.put"
    assert spans[1]["name"] == f"S{id + 1}"

    for span in (spans[2], spans[3]):
        assert span["attributes"]["sentry.op"] == "cache.get"
        assert span["name"] == f"S{id}"
        assert span["attributes"]["cache.hit"]
        assert span["attributes"]["cache.item_size"] == 10

    for span in (spans[4], spans[5]):
        assert span["attributes"]["sentry.op"] == "cache.get"
        assert span["name"] == f"S{id + 1}"
        assert span["attributes"]["cache.hit"]
        assert span["attributes"]["cache.item_size"] == 0

    for span in (spans[6], spans[7]):
        assert span["attributes"]["sentry.op"] == "cache.get"
        assert span["name"] == f"S{id + 2}"
        assert not span["attributes"]["cache.hit"]
        assert "cache.item_size" not in span["attributes"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_get_many(
    sentry_init,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )

    id = os.getpid()

    from django.core.cache import cache

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        cache.get_many([f"S{id}", f"S{id + 1}"])
        cache.set(f"S{id}", "Sensitive1")
        cache.get_many([f"S{id}", f"S{id + 1}"])

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 8

    assert spans[2]["attributes"]["sentry.op"] == "cache.get"
    assert spans[2]["name"] == f"S{id}, S{id + 1}"
    assert not spans[2]["attributes"]["cache.hit"]

    assert spans[0]["attributes"]["sentry.op"] == "cache.get"
    assert spans[0]["name"] == f"S{id}"
    assert not spans[0]["attributes"]["cache.hit"]

    assert spans[1]["attributes"]["sentry.op"] == "cache.get"
    assert spans[1]["name"] == f"S{id + 1}"
    assert not spans[1]["attributes"]["cache.hit"]

    assert spans[3]["attributes"]["sentry.op"] == "cache.put"
    assert spans[3]["name"] == f"S{id}"

    assert spans[6]["attributes"]["sentry.op"] == "cache.get"
    assert spans[6]["name"] == f"S{id}, S{id + 1}"
    assert spans[6]["attributes"]["cache.hit"]

    assert spans[4]["attributes"]["sentry.op"] == "cache.get"
    assert spans[4]["name"] == f"S{id}"
    assert spans[4]["attributes"]["cache.hit"]

    assert spans[5]["attributes"]["sentry.op"] == "cache.get"
    assert spans[5]["name"] == f"S{id + 1}"
    assert not spans[5]["attributes"]["cache.hit"]


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_cache_spans_set_many(
    sentry_init,
    capture_items,
    use_django_caching,
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
        trace_lifecycle="stream",
    )

    id = os.getpid()

    from django.core.cache import cache

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        cache.set_many({f"S{id}": "Sensitive1", f"S{id + 1}": "Sensitive2"})
        cache.get(f"S{id}")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 5

    assert spans[2]["attributes"]["sentry.op"] == "cache.put"
    assert spans[2]["name"] == f"S{id}, S{id + 1}"

    assert spans[0]["attributes"]["sentry.op"] == "cache.put"
    assert spans[0]["name"] == f"S{id}"

    assert spans[1]["attributes"]["sentry.op"] == "cache.put"
    assert spans[1]["name"] == f"S{id + 1}"

    assert spans[3]["attributes"]["sentry.op"] == "cache.get"
    assert spans[3]["name"] == f"S{id}"


@pytest.mark.forked
@pytest_mark_django_db_decorator()
def test_span_origin_cache(
    sentry_init,
    client,
    capture_items,
    use_django_caching,
):
    sentry_init(
        integrations=[
            DjangoIntegration(
                middleware_spans=True,
                signals_spans=True,
                cache_spans=True,
            )
        ],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    cache_span_found = False
    items = capture_items("span")

    client.get(reverse("cached_view"))

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    assert spans[1]["attributes"]["sentry.origin"] == "auto.http.django"

    for span in spans:
        assert span["attributes"]["sentry.origin"] == "auto.http.django"
        if span["attributes"]["sentry.op"].startswith("cache."):
            cache_span_found = True

    assert cache_span_found

import functools
from typing import TYPE_CHECKING

from django import VERSION as DJANGO_VERSION
from django.core.cache import CacheHandler

from sentry_sdk import Hub
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk._compat import text_type


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable


METHODS_TO_INSTRUMENT = [
    "get",
    "get_many",
]


def _patch_cache_method(cache, method_name):
    # type: (CacheHandler, str) -> None
    from sentry_sdk.integrations.django import DjangoIntegration

    def _instrument_call(cache, method_name, original_method, args, kwargs):
        # type: (CacheHandler, str, Callable[..., Any], Any, Any) -> Any
        hub = Hub.current
        integration = hub.get_integration(DjangoIntegration)
        if integration is None or not integration.cache_spans:
            return None

        description = "{} {}".format(method_name, " ".join(args))

        with hub.start_span(op=OP.CACHE, description=description) as span:
            cache._sentry_recording = True
            value = original_method(*args, **kwargs)
            cache._sentry_recording = False

            if value:
                span.set_data(SPANDATA.CACHE_HIT, True)

                size = len(text_type(value).encode("utf-8"))
                span.set_data(SPANDATA.CACHE_ITEM_SIZE, size)

            else:
                span.set_data(SPANDATA.CACHE_HIT, False)

            return value

    original_method = getattr(cache, method_name)

    @functools.wraps(original_method)
    def sentry_method(*args, **kwargs):
        # type: (*Any, **Any) -> Any

        # If this call is being made inside another call, don't instrument it.
        if cache._sentry_recording:
            return original_method(*args, **kwargs)
        else:
            return _instrument_call(cache, method_name, original_method, args, kwargs)

    setattr(cache, method_name, sentry_method)


def _patch_cache(cache):
    # type: (CacheHandler) -> None
    if not hasattr(cache, "_sentry_patched"):
        for method_name in METHODS_TO_INSTRUMENT:
            _patch_cache_method(cache, method_name)
        cache._sentry_patched = True
        cache._sentry_recording = False


def patch_caching():
    # type: () -> None
    from sentry_sdk.integrations.django import DjangoIntegration

    if not hasattr(CacheHandler, "_sentry_patched"):
        if DJANGO_VERSION < (3, 2):
            original_get_item = CacheHandler.__getitem__

            @functools.wraps(original_get_item)
            def sentry_get_item(self, alias):
                # type: (CacheHandler, str) -> Any
                cache = original_get_item(self, alias)

                integration = Hub.current.get_integration(DjangoIntegration)
                if integration and integration.cache_spans:
                    _patch_cache(cache)

                return cache

            CacheHandler.__getitem__ = sentry_get_item
            CacheHandler._sentry_patched = True

        else:
            original_create_connection = CacheHandler.create_connection

            @functools.wraps(original_create_connection)
            def sentry_create_connection(self, alias):
                # type: (CacheHandler, str) -> Any
                cache = original_create_connection(self, alias)

                integration = Hub.current.get_integration(DjangoIntegration)
                if integration and integration.cache_spans:
                    _patch_cache(cache)

                return cache

            CacheHandler.create_connection = sentry_create_connection
            CacheHandler._sentry_patched = True

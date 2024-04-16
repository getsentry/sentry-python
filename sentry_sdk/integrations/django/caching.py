import functools
from typing import TYPE_CHECKING

from django import VERSION as DJANGO_VERSION
from django.core.cache import CacheHandler

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.utils import ensure_integration_enabled


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Optional


# TODO: In new Django there is also `aget` and `aget_many` methods
# TODO: for also creating spans for setting cache there is a `set`, `aset`, `add`, `aadd` methods
#       see https://github.com/django/django/blob/main/django/core/cache/backends/base.py
METHODS_TO_INSTRUMENT = [
    # "set", 
    "get",
    "get_many",
]

def _get_timeout(args, kwargs):
    # type: (Any, Any) -> Optional[int]
    if args is not None and len(args) >= 3:
        return args[2]
    return None


def _get_key(args, kwargs):
    # type: (Any, Any) -> str
    if args is not None and len(args) >= 1:
        return str(args[0])
    elif kwargs is not None and "key" in kwargs:
        return str(kwargs["key"])

    return ""


def _get_span_description(method_name, args, kwargs):
    # type: (str, Any, Any) -> str
    description = "{} {}".format(
        method_name, 
        _get_key(args, kwargs), 
    )
    return description


def _patch_cache_method(cache, method_name):
    # type: (CacheHandler, str) -> None
    from sentry_sdk.integrations.django import DjangoIntegration

    original_method = getattr(cache, method_name)

    @ensure_integration_enabled(DjangoIntegration, original_method)
    def _instrument_call(cache, method_name, original_method, args, kwargs):
        # type: (CacheHandler, str, Callable[..., Any], Any, Any) -> Any
        import ipdb; ipdb.set_trace()

        is_set_operation = method_name == "set"

        op = OP.CACHE_SET_ITEM if is_set_operation else OP.CACHE_GET_ITEM
        description = _get_span_description(method_name, args, kwargs)

        with sentry_sdk.start_span(op=op, description=description) as span:
            value = original_method(*args, **kwargs)

            key = _get_key(args, kwargs)
            if key != "":
                span.set_data(SPANDATA.CACHE_KEY, key)

            if is_set_operation:
                timeout = _get_timeout(args, kwargs)
                if timeout is not None:
                    span.set_data(SPANDATA.CACHE_TTL, timeout)
            else:
                if value:
                    span.set_data(SPANDATA.CACHE_HIT, True)

                    size = len(str(value))
                    span.set_data(SPANDATA.CACHE_ITEM_SIZE, size)

                else:
                    span.set_data(SPANDATA.CACHE_HIT, False)

            return value

    @functools.wraps(original_method)
    def sentry_method(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        return _instrument_call(cache, method_name, original_method, args, kwargs)

    setattr(cache, method_name, sentry_method)


def _patch_cache(cache):
    # type: (CacheHandler) -> None
    if not hasattr(cache, "_sentry_patched"):
        # TODO: Here we can also patch the set methods (see TODO above)
        for method_name in METHODS_TO_INSTRUMENT:
            _patch_cache_method(cache, method_name)
        cache._sentry_patched = True


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

                integration = sentry_sdk.get_client().get_integration(DjangoIntegration)
                if integration is not None and integration.cache_spans:
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

                integration = sentry_sdk.get_client().get_integration(DjangoIntegration)
                if integration is not None and integration.cache_spans:
                    _patch_cache(cache)

                return cache

            CacheHandler.create_connection = sentry_create_connection
            CacheHandler._sentry_patched = True

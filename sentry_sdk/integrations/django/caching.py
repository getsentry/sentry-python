import functools
from typing import TYPE_CHECKING

from django import VERSION as DJANGO_VERSION
from django.core.cache import CacheHandler

from sentry_sdk import Hub
from sentry_sdk.consts import OP, SPANDATA


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable


METHODS_TO_INSTRUMENT = [
    "get",
    "get_many",
]


def _get_span_description(method_name: str, args: Any, kwargs: Any) -> str:
    description = "{} ".format(method_name)

    if args is not None and len(args) >= 1:
        description += str(args[0])
    elif kwargs is not None and "key" in kwargs:
        description += str(kwargs["key"])

    return description


def _patch_cache_method(cache: CacheHandler, method_name: str) -> None:
    from sentry_sdk.integrations.django import DjangoIntegration

    def _instrument_call(
        cache: CacheHandler,
        method_name: str,
        original_method: Callable[..., Any],
        args: Any,
        kwargs: Any,
    ) -> Any:
        hub = Hub.current
        integration = hub.get_integration(DjangoIntegration)
        if integration is None or not integration.cache_spans:
            return original_method(*args, **kwargs)

        description = _get_span_description(method_name, args, kwargs)

        with hub.start_span(op=OP.CACHE_GET_ITEM, description=description) as span:
            value = original_method(*args, **kwargs)

            if value:
                span.set_data(SPANDATA.CACHE_HIT, True)

                size = len(str(value))
                span.set_data(SPANDATA.CACHE_ITEM_SIZE, size)

            else:
                span.set_data(SPANDATA.CACHE_HIT, False)

            return value

    original_method = getattr(cache, method_name)

    @functools.wraps(original_method)
    def sentry_method(*args: Any, **kwargs: Any) -> Any:
        return _instrument_call(cache, method_name, original_method, args, kwargs)

    setattr(cache, method_name, sentry_method)


def _patch_cache(cache: CacheHandler) -> None:
    if not hasattr(cache, "_sentry_patched"):
        for method_name in METHODS_TO_INSTRUMENT:
            _patch_cache_method(cache, method_name)
        cache._sentry_patched = True


def patch_caching() -> None:
    from sentry_sdk.integrations.django import DjangoIntegration

    if not hasattr(CacheHandler, "_sentry_patched"):
        if DJANGO_VERSION < (3, 2):
            original_get_item = CacheHandler.__getitem__

            @functools.wraps(original_get_item)
            def sentry_get_item(self: CacheHandler, alias: str) -> Any:
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
            def sentry_create_connection(self: CacheHandler, alias: str) -> Any:
                cache = original_create_connection(self, alias)

                integration = Hub.current.get_integration(DjangoIntegration)
                if integration and integration.cache_spans:
                    _patch_cache(cache)

                return cache

            CacheHandler.create_connection = sentry_create_connection
            CacheHandler._sentry_patched = True

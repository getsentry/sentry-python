import functools
from typing import TYPE_CHECKING
from urllib3.util import parse_url as urlparse

from django import VERSION as DJANGO_VERSION
from django.core.cache import CacheHandler

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.utils import capture_internal_exceptions, ensure_integration_enabled


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import tuple
    from typing import Optional


METHODS_TO_INSTRUMENT = [
    "set",
    "get",
]


def _get_key(args, kwargs):
    # type: (list[Any], dict[str, Any]) -> str
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


def _patch_cache_method(cache, method_name, address, port):
    # type: (CacheHandler, str, Optional[str], Optional[str]) -> None
    from sentry_sdk.integrations.django import DjangoIntegration

    original_method = getattr(cache, method_name)

    @ensure_integration_enabled(DjangoIntegration, original_method)
    def _instrument_call(
        cache, method_name, original_method, args, kwargs, address, port
    ):
        # type: (CacheHandler, str, Callable[..., Any], Any, Any, Optional[str], Optional[str]) -> Any
        integration = sentry_sdk.get_client().get_integration(DjangoIntegration)
        is_set_operation = method_name.startswith("set")
        is_get_operation = not is_set_operation

        op = OP.CACHE_SET_ITEM if is_set_operation else OP.CACHE_GET_ITEM
        description = _get_span_description(method_name, args, kwargs)

        with sentry_sdk.start_span(op=op, description=description) as span:
            value = original_method(*args, **kwargs)

            with capture_internal_exceptions():
                if address is not None:
                    # Strip eventually contained username and password in url style location
                    if "://" in address:
                        parsed_url = urlparse(address)
                        address = "{}://{}{}".format(
                            parsed_url.scheme or "",
                            parsed_url.netloc or "",
                            parsed_url.path or "",
                        )

                    span.set_data(SPANDATA.NETWORK_PEER_ADDRESS, address)

                if port is not None:
                    span.set_data(SPANDATA.NETWORK_PEER_PORT, int(port))

                key = _get_key(args, kwargs)
                if key != "":
                    span.set_data(SPANDATA.CACHE_KEY, key)

                item_size = None
                if is_get_operation:
                    if value:
                        if integration.cache_spans_add_item_size:
                            item_size = len(str(value))
                        span.set_data(SPANDATA.CACHE_HIT, True)
                    else:
                        span.set_data(SPANDATA.CACHE_HIT, False)
                elif integration.cache_spans_add_item_size:
                    item_size = len(str(args[1]))

                if item_size is not None:
                    span.set_data(SPANDATA.CACHE_ITEM_SIZE, item_size)

            return value

    @functools.wraps(original_method)
    def sentry_method(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        return _instrument_call(
            cache, method_name, original_method, args, kwargs, address, port
        )

    setattr(cache, method_name, sentry_method)


def _patch_cache(cache, address=None, port=None):
    # type: (CacheHandler, Optional[str], Optional[str]) -> None
    if not hasattr(cache, "_sentry_patched"):
        for method_name in METHODS_TO_INSTRUMENT:
            _patch_cache_method(cache, method_name, address, port)
        cache._sentry_patched = True


def _get_address_port(settings):
    # type: (dict[str, Any]) -> tuple[Optional[str], Optional[str]]
    address, port = None, None
    location = settings.get("LOCATION")
    # TODO: location can also be an array of locations
    # see: https://docs.djangoproject.com/en/5.0/topics/cache/#redis
    if isinstance(location, str):
        if ":" in location:
            address, port = location.rsplit(":", 1)
        else:
            address, port = location, None

    return address, port


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
                    from django.conf import settings

                    address, port = _get_address_port(
                        settings.CACHES[alias or "default"]
                    )

                    _patch_cache(cache, address, port)

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
                    address, port = _get_address_port(self.settings[alias or "default"])

                    _patch_cache(cache, address, port)

                return cache

            CacheHandler.create_connection = sentry_create_connection
            CacheHandler._sentry_patched = True

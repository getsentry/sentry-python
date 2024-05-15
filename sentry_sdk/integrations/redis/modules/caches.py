"""
Code used for the Queries modules in Sentry
"""

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis.utils import _get_safe_key


if TYPE_CHECKING:
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.tracing import Span
    from typing import Any


def _get_cache_span_description(integration, command_name, args, kwargs):
    # type: (RedisIntegration, str, tuple[Any], dict[str, Any]) -> str
    description = "{} {}".format(
        command_name,
        _get_safe_key(command_name, args, kwargs),
    )

    data_should_be_truncated = (
        integration.max_data_size and len(description) > integration.max_data_size
    )
    if data_should_be_truncated:
        description = description[: integration.max_data_size - len("...")] + "..."

    return description


def _set_cache_data(span, properties):
    # type: (Span, dict[str, Any]) -> None
    # .. add len(value) as cache.item_size to outer span
    # .. add more data to cache key from https://develop.sentry.dev/sdk/performance/modules/caches/
    span.set_data(SPANDATA.NETWORK_PEER_ADDRESS, "TODO!!!! localhost")
    span.set_data(SPANDATA.NETWORK_PEER_PORT, 0000)
    span.set_data(SPANDATA.CACHE_KEY, properties["key"])
    span.set_data(SPANDATA.CACHE_HIT, True)
    span.set_data(SPANDATA.CACHE_ITEM_SIZE, 000)

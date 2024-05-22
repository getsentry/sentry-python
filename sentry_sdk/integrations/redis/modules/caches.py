"""
Code used for the Caches module in Sentry
"""

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.redis.utils import _get_safe_key
from sentry_sdk.utils import capture_internal_exceptions

GET_COMMANDS = ("get", "mget")
SET_COMMANDS = ("set", "setex")

if TYPE_CHECKING:
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.tracing import Span
    from typing import Any, Optional


def _get_op(name):
    # type: (str) -> Optional[str]
    op = None
    if name.lower() in GET_COMMANDS:
        op = OP.CACHE_GET
    elif name.lower() in SET_COMMANDS:
        op = OP.CACHE_SET

    return op


def _compile_cache_span_properties(integration, redis_command, args, kwargs):
    # type: (RedisIntegration, str, tuple[Any, ...], dict[str, Any]) -> dict[str, Any]
    key = _get_safe_key(args, kwargs)

    is_cache_key = False
    for prefix in integration.cache_prefixes:
        if key.startswith(prefix):
            is_cache_key = True
            break

    value = None
    if redis_command.lower() in SET_COMMANDS:
        value = args[-1]

    properties = {
        "op": _get_op(redis_command),
        "description": _get_cache_span_description(
            integration, redis_command, args, kwargs
        ),
        "key": key,
        "redis_command": redis_command.lower(),
        "is_cache_key": is_cache_key,
        "value": value,
    }

    return properties


def _get_cache_span_description(integration, command_name, args, kwargs):
    # type: (RedisIntegration, str, tuple[Any, ...], dict[str, Any]) -> str
    description = _get_safe_key(args, kwargs)

    data_should_be_truncated = (
        integration.max_data_size and len(description) > integration.max_data_size
    )
    if data_should_be_truncated:
        description = description[: integration.max_data_size - len("...")] + "..."

    return description


def _set_cache_data(span, redis_client, properties, return_value):
    # type: (Span, Any, dict[str, Any], Optional[Any]) -> None
    with capture_internal_exceptions():
        span.set_data(SPANDATA.CACHE_KEY, properties["key"])

        if properties["redis_command"] in GET_COMMANDS:
            if return_value is not None:
                span.set_data(SPANDATA.CACHE_HIT, True)
                size = (
                    len(return_value.encode("utf-8"))
                    if not isinstance(return_value, bytes)
                    else len(return_value)
                )
                span.set_data(SPANDATA.CACHE_ITEM_SIZE, size)
            else:
                span.set_data(SPANDATA.CACHE_HIT, False)

        elif properties["redis_command"] in SET_COMMANDS:
            if properties["value"] is not None:
                size = (
                    len(properties["value"].encode("utf-8"))
                    if not isinstance(properties["value"], bytes)
                    else len(properties["value"])
                )
                span.set_data(SPANDATA.CACHE_ITEM_SIZE, size)

        try:
            connection_params = redis_client.connection_pool.connection_kwargs
        except AttributeError:
            # If it is a cluster, there is no connection_pool attribute so we
            # need to get the default node from the cluster instance
            default_node = redis_client.get_default_node()
            connection_params = {
                "host": default_node.host,
                "port": default_node.port,
            }

        host = connection_params.get("host")
        if host is not None:
            span.set_data(SPANDATA.NETWORK_PEER_ADDRESS, host)

        port = connection_params.get("port")
        if port is not None:
            span.set_data(SPANDATA.NETWORK_PEER_PORT, port)

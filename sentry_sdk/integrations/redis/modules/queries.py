"""
Code used for the Queries module in Sentry
"""

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.redis.utils import _get_safe_command
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.utils import capture_internal_exceptions

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.tracing import Span
    from typing import Any, Union


def _compile_db_span_properties(
    integration: "RedisIntegration", redis_command: str, args: "tuple[Any, ...]"
) -> "dict[str, Any]":
    description = _get_db_span_description(integration, redis_command, args)

    properties = {
        "op": OP.DB_REDIS,
        "description": description,
    }

    return properties


def _get_db_span_description(
    integration: "RedisIntegration", command_name: str, args: "tuple[Any, ...]"
) -> str:
    description = command_name

    with capture_internal_exceptions():
        description = _get_safe_command(command_name, args)

    if integration.max_data_size and len(description) > integration.max_data_size:
        description = description[: integration.max_data_size - len("...")] + "..."

    return description


def _set_db_data_on_span(
    span: "Union[Span, StreamedSpan]", connection_params: "dict[str, Any]"
) -> None:
    db = connection_params.get("db")
    host = connection_params.get("host")
    port = connection_params.get("port")

    if isinstance(span, StreamedSpan):
        span.set_attribute(SPANDATA.DB_SYSTEM_NAME, "redis")
        span.set_attribute(SPANDATA.DB_DRIVER_NAME, "redis-py")

        if db is not None:
            span.set_attribute(SPANDATA.DB_NAMESPACE, str(db))

        if host is not None:
            span.set_attribute(SPANDATA.SERVER_ADDRESS, host)

        if port is not None:
            span.set_attribute(SPANDATA.SERVER_PORT, port)

    else:
        span.set_data(SPANDATA.DB_SYSTEM, "redis")
        span.set_data(SPANDATA.DB_DRIVER_NAME, "redis-py")

        if db is not None:
            span.set_data(SPANDATA.DB_NAME, str(db))

        if host is not None:
            span.set_data(SPANDATA.SERVER_ADDRESS, host)

        if port is not None:
            span.set_data(SPANDATA.SERVER_PORT, port)


def _set_db_data(
    span: "Union[Span, StreamedSpan]", redis_instance: "Redis[Any]"
) -> None:
    try:
        _set_db_data_on_span(span, redis_instance.connection_pool.connection_kwargs)
    except AttributeError:
        pass  # connections_kwargs may be missing in some cases

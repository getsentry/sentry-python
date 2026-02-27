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
    if isinstance(span, StreamedSpan):
        set_on_span = span.set_attribute
    else:
        set_on_span = span.set_data

    set_on_span(SPANDATA.DB_SYSTEM, "redis")

    db = connection_params.get("db")
    if db is not None:
        set_on_span(SPANDATA.DB_NAME, str(db))

    host = connection_params.get("host")
    if host is not None:
        set_on_span(SPANDATA.SERVER_ADDRESS, host)

    port = connection_params.get("port")
    if port is not None:
        set_on_span(SPANDATA.SERVER_PORT, port)


def _set_db_data(
    span: "Union[Span, StreamedSpan]", redis_instance: "Redis[Any]"
) -> None:
    try:
        _set_db_data_on_span(span, redis_instance.connection_pool.connection_kwargs)
    except AttributeError:
        pass  # connections_kwargs may be missing in some cases

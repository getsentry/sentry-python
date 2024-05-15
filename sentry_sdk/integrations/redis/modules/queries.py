"""
Code used for the Queries modules in Sentry
"""

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import SPANDATA


if TYPE_CHECKING:
    from redis import Redis
    from sentry_sdk.tracing import Span
    from typing import Any


def _set_db_data_on_span(span, connection_params):
    # type: (Span, dict[str, Any]) -> None
    span.set_data(SPANDATA.DB_SYSTEM, "redis")

    db = connection_params.get("db")
    if db is not None:
        span.set_data(SPANDATA.DB_NAME, str(db))

    host = connection_params.get("host")
    if host is not None:
        span.set_data(SPANDATA.SERVER_ADDRESS, host)

    port = connection_params.get("port")
    if port is not None:
        span.set_data(SPANDATA.SERVER_PORT, port)


def _set_db_data(span, redis_instance):
    # type: (Span, Redis[Any]) -> None
    try:
        _set_db_data_on_span(span, redis_instance.connection_pool.connection_kwargs)
    except AttributeError:
        pass  # connections_kwargs may be missing in some cases

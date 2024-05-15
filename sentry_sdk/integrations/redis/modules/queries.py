"""
Code used for the Queries modules in Sentry
"""

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.integrations.redis.utils import _set_db_data_on_span


if TYPE_CHECKING:
    from redis import Redis
    from sentry_sdk.tracing import Span
    from typing import Any


def _set_db_data(span, redis_instance):
    # type: (Span, Redis[Any]) -> None
    try:
        _set_db_data_on_span(span, redis_instance.connection_pool.connection_kwargs)
    except AttributeError:
        pass  # connections_kwargs may be missing in some cases

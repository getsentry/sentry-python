"""
Instrumentation for Redis

https://github.com/redis/redis-py
"""

from typing import TYPE_CHECKING

from sentry_sdk.integrations.redis._async_common import (
    patch_redis_async_client,
    patch_redis_async_pipeline,
)
from sentry_sdk.integrations.redis._sync_common import (
    patch_redis_client,
    patch_redis_pipeline,
)
from sentry_sdk.integrations.redis.modules.queries import _set_db_data

if TYPE_CHECKING:
    from typing import Any, Sequence


def _get_redis_command_args(command: "Any") -> "Sequence[Any]":
    return command[0]


def _patch_redis(StrictRedis: "Any", client: "Any") -> None:  # noqa: N803
    import redis.asyncio

    patch_redis_client(
        StrictRedis,
        is_cluster=False,
        set_db_data_fn=_set_db_data,
    )
    patch_redis_pipeline(
        client.Pipeline,
        is_cluster=False,
        get_command_args_fn=_get_redis_command_args,
        set_db_data_fn=_set_db_data,
    )

    patch_redis_async_client(
        redis.asyncio.client.StrictRedis,
        is_cluster=False,
        set_db_data_fn=_set_db_data,
    )
    patch_redis_async_pipeline(
        redis.asyncio.client.Pipeline,
        False,
        _get_redis_command_args,
        set_db_data_fn=_set_db_data,
    )

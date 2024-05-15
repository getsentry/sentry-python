"""
Instrumentation for rediscluster

https://github.com/salimane/rediscluster-py
"""

from sentry_sdk.integrations.redis.common_sync import (
    patch_redis_client,
    patch_redis_pipeline,
)
from sentry_sdk.integrations.redis.modules.caches import _set_cache_data
from sentry_sdk.integrations.redis.modules.queries import _set_db_data
from sentry_sdk.integrations.redis.utils import _parse_rediscluster_command


def _patch_rediscluster():
    # type: () -> None
    try:
        import rediscluster  # type: ignore
    except ImportError:
        return

    patch_redis_client(
        rediscluster.RedisCluster,
        is_cluster=True,
        set_db_data_fn=_set_db_data,
        set_cache_data_fn=_set_cache_data,
    )

    # up to v1.3.6, __version__ attribute is a tuple
    # from v2.0.0, __version__ is a string and VERSION a tuple
    version = getattr(rediscluster, "VERSION", rediscluster.__version__)

    # StrictRedisCluster was introduced in v0.2.0 and removed in v2.0.0
    # https://github.com/Grokzen/redis-py-cluster/blob/master/docs/release-notes.rst
    if (0, 2, 0) < version < (2, 0, 0):
        pipeline_cls = rediscluster.pipeline.StrictClusterPipeline
        patch_redis_client(
            rediscluster.StrictRedisCluster,
            is_cluster=True,
            set_db_data_fn=_set_db_data,
            set_cache_data_fn=_set_cache_data,
        )
    else:
        pipeline_cls = rediscluster.pipeline.ClusterPipeline

    patch_redis_pipeline(
        pipeline_cls,
        is_cluster=True,
        get_command_args_fn=_parse_rediscluster_command,
        set_db_data_fn=_set_db_data,
    )

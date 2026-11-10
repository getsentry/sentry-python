"""
Instrumentation for redis-py-cluster
The project redis-py-cluster is EOL and was integrated into redis-py starting from version 4.1.0 (Dec 26, 2021).

https://github.com/grokzen/redis-py-cluster
"""

from sentry_sdk.integrations.redis._sync_common import (
    patch_redis_client,
    patch_redis_pipeline,
)
from sentry_sdk.integrations.redis.modules.queries import _set_db_data
from sentry_sdk.integrations.redis.utils import _parse_rediscluster_command


def _patch_rediscluster() -> None:
    try:
        import rediscluster  # type: ignore
    except ImportError:
        return

    patch_redis_client(
        rediscluster.RedisCluster,
        is_cluster=True,
        set_db_data_fn=_set_db_data,
    )
    patch_redis_pipeline(
        rediscluster.pipeline.ClusterPipeline,
        is_cluster=True,
        get_command_args_fn=_parse_rediscluster_command,
        set_db_data_fn=_set_db_data,
    )

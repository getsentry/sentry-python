"""
Instrumentation for RedisCluster

https://github.com/redis/redis-py/blob/master/redis/cluster.py
"""

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.integrations.redis.common_sync import (
    patch_redis_client,
    patch_redis_pipeline,
)
from sentry_sdk.integrations.redis.modules.queries import _set_db_data_on_span
from sentry_sdk.integrations.redis.utils import _parse_rediscluster_command

from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Optional
    from redis import RedisCluster
    from redis.asyncio.cluster import (
        RedisCluster as AsyncRedisCluster,
        ClusterPipeline as AsyncClusterPipeline,
    )
    from sentry_sdk.tracing import Span


def _set_async_cluster_db_data(span, async_redis_cluster_instance):
    # type: (Span, AsyncRedisCluster[Any]) -> None
    default_node = async_redis_cluster_instance.get_default_node()
    if default_node is not None and default_node.connection_kwargs is not None:
        _set_db_data_on_span(span, default_node.connection_kwargs)


def _set_async_cluster_pipeline_db_data(span, async_redis_cluster_pipeline_instance):
    # type: (Span, AsyncClusterPipeline[Any]) -> None
    with capture_internal_exceptions():
        _set_async_cluster_db_data(
            span,
            # the AsyncClusterPipeline has always had a `_client` attr but it is private so potentially problematic and mypy
            # does not recognize it - see https://github.com/redis/redis-py/blame/v5.0.0/redis/asyncio/cluster.py#L1386
            async_redis_cluster_pipeline_instance._client,  # type: ignore[attr-defined]
        )


def _set_cluster_db_data(span, redis_cluster_instance):
    # type: (Span, RedisCluster[Any]) -> None
    default_node = redis_cluster_instance.get_default_node()
    if default_node is not None:
        _set_db_data_on_span(
            span, {"host": default_node.host, "port": default_node.port}
        )


def _set_cluster_cache_data(span, redis_client, properties, return_value):
    # type: (Span, Any, dict[str, Any], Optional[Any]) -> None
    import ipdb

    ipdb.set_trace()
    raise NotImplementedError("Cache data is not supported for Redis Cluster")


def _patch_redis_cluster():
    # type: () -> None
    """Patches the cluster module on redis SDK (as opposed to rediscluster library)"""
    try:
        from redis import RedisCluster, cluster
    except ImportError:
        pass
    else:
        patch_redis_client(
            RedisCluster,
            is_cluster=True,
            set_db_data_fn=_set_cluster_db_data,
            set_cache_data_fn=_set_cluster_cache_data,
        )
        patch_redis_pipeline(
            cluster.ClusterPipeline,
            is_cluster=True,
            get_command_args_fn=_parse_rediscluster_command,
            set_db_data_fn=_set_cluster_db_data,
        )

    try:
        from redis.asyncio import cluster as async_cluster
    except ImportError:
        pass
    else:
        from sentry_sdk.integrations.redis.common_async import (
            patch_redis_async_client,
            patch_redis_async_pipeline,
        )

        patch_redis_async_client(
            async_cluster.RedisCluster,
            is_cluster=True,
            set_db_data_fn=_set_async_cluster_db_data,
            set_cache_data_fn=_set_cluster_cache_data,
        )
        patch_redis_async_pipeline(
            async_cluster.ClusterPipeline,
            True,
            _parse_rediscluster_command,
            set_db_data_fn=_set_async_cluster_pipeline_db_data,
        )

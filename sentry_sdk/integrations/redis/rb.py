"""
Instrumentation for Redis Blaster (rb)

https://github.com/getsentry/rb
"""

from sentry_sdk.integrations.redis.common_sync import patch_redis_client
from sentry_sdk.integrations.redis.modules.caches import _set_cache_data
from sentry_sdk.integrations.redis.modules.queries import _set_db_data


def _patch_rb():
    # type: () -> None
    try:
        import rb.clients  # type: ignore
    except ImportError:
        pass
    else:
        patch_redis_client(
            rb.clients.FanoutClient,
            is_cluster=False,
            set_db_data_fn=_set_db_data,
            set_cache_data_fn=_set_cache_data,
        )
        patch_redis_client(
            rb.clients.MappingClient,
            is_cluster=False,
            set_db_data_fn=_set_db_data,
            set_cache_data_fn=_set_cache_data,
        )
        patch_redis_client(
            rb.clients.RoutingClient,
            is_cluster=False,
            set_db_data_fn=_set_db_data,
            set_cache_data_fn=_set_cache_data,
        )

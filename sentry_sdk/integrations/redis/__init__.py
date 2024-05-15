from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.redis.consts import _DEFAULT_MAX_DATA_SIZE
from sentry_sdk.integrations.redis.rb import _patch_rb
from sentry_sdk.integrations.redis.redis import _patch_redis
from sentry_sdk.integrations.redis.redis_cluster import _patch_redis_cluster
from sentry_sdk.integrations.redis.rediscluster import _patch_rediscluster
from sentry_sdk.utils import logger


class RedisIntegration(Integration):
    identifier = "redis"

    def __init__(self, max_data_size=_DEFAULT_MAX_DATA_SIZE):
        # type: (int) -> None
        self.max_data_size = max_data_size
        # TODO: add some prefix that users can set to specify a cache key
        # GitHub issue: https://github.com/getsentry/sentry-python/issues/2965

    @staticmethod
    def setup_once():
        # type: () -> None
        try:
            from redis import StrictRedis, client
        except ImportError:
            raise DidNotEnable("Redis client not installed")

        _patch_redis(StrictRedis, client)
        _patch_redis_cluster()
        _patch_rb()

        try:
            _patch_rediscluster()
        except Exception:
            logger.exception("Error occurred while patching `rediscluster` library")

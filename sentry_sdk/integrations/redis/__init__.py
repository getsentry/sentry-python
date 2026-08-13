from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations.redis.rb import _patch_rb
from sentry_sdk.integrations.redis.redis import _patch_redis
from sentry_sdk.integrations.redis.redis_cluster import _patch_redis_cluster
from sentry_sdk.utils import parse_version

if TYPE_CHECKING:
    from typing import Optional


class RedisIntegration(Integration):
    identifier = "redis"

    def __init__(
        self,
        cache_prefixes: "Optional[list[str]]" = None,
    ) -> None:
        self.cache_prefixes = cache_prefixes if cache_prefixes is not None else []

    @staticmethod
    def setup_once() -> None:
        try:
            from redis import __version__ as REDIS_VERSION
        except ImportError:
            raise DidNotEnable("Redis client not installed")

        version = parse_version(REDIS_VERSION)
        _check_minimum_version(RedisIntegration, version)

        _patch_redis()
        _patch_redis_cluster()
        _patch_rb()

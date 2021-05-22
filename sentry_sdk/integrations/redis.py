from __future__ import absolute_import

from sentry_sdk import Hub
from sentry_sdk.utils import capture_internal_exceptions, logger
from sentry_sdk.integrations import Integration

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any

_SINGLE_KEY_COMMANDS = frozenset(
    ["decr", "decrby", "get", "incr", "incrby", "pttl", "set", "setex", "setnx", "ttl"]
)
_MULTI_KEY_COMMANDS = frozenset(["del", "touch", "unlink"])


def _patch_rediscluster():
    # type: () -> None
    try:
        import rediscluster  # type: ignore
    except ImportError:
        return

    patch_redis_client(rediscluster.RedisCluster)

    # up to v1.3.6, __version__ attribute is a tuple
    # from v2.0.0, __version__ is a string and VERSION a tuple
    version = getattr(rediscluster, "VERSION", rediscluster.__version__)

    # StrictRedisCluster was introduced in v0.2.0 and removed in v2.0.0
    # https://github.com/Grokzen/redis-py-cluster/blob/master/docs/release-notes.rst
    if (0, 2, 0) < version < (2, 0, 0):
        patch_redis_client(rediscluster.StrictRedisCluster)


class RedisIntegration(Integration):
    identifier = "redis"

    @staticmethod
    def setup_once():
        # type: () -> None
        import redis

        patch_redis_client(redis.StrictRedis)

        try:
            import rb.clients  # type: ignore
        except ImportError:
            pass
        else:
            patch_redis_client(rb.clients.FanoutClient)
            patch_redis_client(rb.clients.MappingClient)
            patch_redis_client(rb.clients.RoutingClient)

        try:
            _patch_rediscluster()
        except Exception:
            logger.exception("Error occurred while patching `rediscluster` library")


def patch_redis_client(cls):
    # type: (Any) -> None
    """
    This function can be used to instrument custom redis client classes or
    subclasses.
    """

    old_execute_command = cls.execute_command

    def sentry_patched_execute_command(self, name, *args, **kwargs):
        # type: (Any, str, *Any, **Any) -> Any
        hub = Hub.current

        if hub.get_integration(RedisIntegration) is None:
            return old_execute_command(self, name, *args, **kwargs)

        description = name

        with capture_internal_exceptions():
            description_parts = [name]
            for i, arg in enumerate(args):
                if i > 10:
                    break

                description_parts.append(repr(arg))

            description = " ".join(description_parts)

        with hub.start_span(op="redis", description=description) as span:
            if name:
                span.set_tag("redis.command", name)

            if name and args:
                name_low = name.lower()
                if (name_low in _SINGLE_KEY_COMMANDS) or (
                    name_low in _MULTI_KEY_COMMANDS and len(args) == 1
                ):
                    span.set_tag("redis.key", args[0])

            return old_execute_command(self, name, *args, **kwargs)

    cls.execute_command = sentry_patched_execute_command

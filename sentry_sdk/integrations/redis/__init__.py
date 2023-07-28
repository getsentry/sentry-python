from __future__ import absolute_import

from sentry_sdk import Hub
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    logger,
)
from sentry_sdk.integrations import Integration, DidNotEnable

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Sequence
    from sentry_sdk.tracing import Span

_SINGLE_KEY_COMMANDS = frozenset(
    ["decr", "decrby", "get", "incr", "incrby", "pttl", "set", "setex", "setnx", "ttl"]
)
_MULTI_KEY_COMMANDS = frozenset(["del", "touch", "unlink"])

_COMMANDS_INCLUDING_SENSITIVE_DATA = [
    "auth",
]

_MAX_NUM_ARGS = 10  # Trim argument lists to this many values
_MAX_NUM_COMMANDS = 10  # Trim command lists to this many values

_DEFAULT_MAX_DATA_SIZE = 1024


def _get_safe_command(name, args):
    # type: (str, Sequence[Any]) -> str
    command_parts = [name]

    for i, arg in enumerate(args):
        if i > _MAX_NUM_ARGS:
            break

        name_low = name.lower()

        if name_low in _COMMANDS_INCLUDING_SENSITIVE_DATA:
            command_parts.append(SENSITIVE_DATA_SUBSTITUTE)
            continue

        arg_is_the_key = i == 0
        if arg_is_the_key:
            command_parts.append(repr(arg))

        else:
            if _should_send_default_pii():
                command_parts.append(repr(arg))
            else:
                command_parts.append(SENSITIVE_DATA_SUBSTITUTE)

    command = " ".join(command_parts)
    return command


def _set_pipeline_data(
    span, is_cluster, get_command_args_fn, is_transaction, command_stack
):
    # type: (Span, bool, Any, bool, Sequence[Any]) -> None
    span.set_tag("redis.is_cluster", is_cluster)
    transaction = is_transaction if not is_cluster else False
    span.set_tag("redis.transaction", transaction)

    commands = []
    for i, arg in enumerate(command_stack):
        if i >= _MAX_NUM_COMMANDS:
            break

        command = get_command_args_fn(arg)
        commands.append(_get_safe_command(command[0], command[1:]))

    span.set_data(
        "redis.commands",
        {
            "count": len(command_stack),
            "first_ten": commands,
        },
    )


def patch_redis_pipeline(pipeline_cls, is_cluster, get_command_args_fn):
    # type: (Any, bool, Any) -> None
    old_execute = pipeline_cls.execute

    def sentry_patched_execute(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        hub = Hub.current

        if hub.get_integration(RedisIntegration) is None:
            return old_execute(self, *args, **kwargs)

        with hub.start_span(
            op=OP.DB_REDIS, description="redis.pipeline.execute"
        ) as span:
            with capture_internal_exceptions():
                _set_pipeline_data(
                    span,
                    is_cluster,
                    get_command_args_fn,
                    self.transaction,
                    self.command_stack,
                )
                span.set_data(SPANDATA.DB_SYSTEM, "redis")

            return old_execute(self, *args, **kwargs)

    pipeline_cls.execute = sentry_patched_execute


def _get_redis_command_args(command):
    # type: (Any) -> Sequence[Any]
    return command[0]


def _parse_rediscluster_command(command):
    # type: (Any) -> Sequence[Any]
    return command.args


def _patch_redis(StrictRedis, client):  # noqa: N803
    # type: (Any, Any) -> None
    patch_redis_client(StrictRedis, is_cluster=False)
    patch_redis_pipeline(client.Pipeline, False, _get_redis_command_args)
    try:
        strict_pipeline = client.StrictPipeline
    except AttributeError:
        pass
    else:
        patch_redis_pipeline(strict_pipeline, False, _get_redis_command_args)

    try:
        import redis.asyncio
    except ImportError:
        pass
    else:
        from sentry_sdk.integrations.redis.asyncio import (
            patch_redis_async_client,
            patch_redis_async_pipeline,
        )

        patch_redis_async_client(redis.asyncio.client.StrictRedis)
        patch_redis_async_pipeline(redis.asyncio.client.Pipeline)


def _patch_rb():
    # type: () -> None
    try:
        import rb.clients  # type: ignore
    except ImportError:
        pass
    else:
        patch_redis_client(rb.clients.FanoutClient, is_cluster=False)
        patch_redis_client(rb.clients.MappingClient, is_cluster=False)
        patch_redis_client(rb.clients.RoutingClient, is_cluster=False)


def _patch_rediscluster():
    # type: () -> None
    try:
        import rediscluster  # type: ignore
    except ImportError:
        return

    patch_redis_client(rediscluster.RedisCluster, is_cluster=True)

    # up to v1.3.6, __version__ attribute is a tuple
    # from v2.0.0, __version__ is a string and VERSION a tuple
    version = getattr(rediscluster, "VERSION", rediscluster.__version__)

    # StrictRedisCluster was introduced in v0.2.0 and removed in v2.0.0
    # https://github.com/Grokzen/redis-py-cluster/blob/master/docs/release-notes.rst
    if (0, 2, 0) < version < (2, 0, 0):
        pipeline_cls = rediscluster.pipeline.StrictClusterPipeline
        patch_redis_client(rediscluster.StrictRedisCluster, is_cluster=True)
    else:
        pipeline_cls = rediscluster.pipeline.ClusterPipeline

    patch_redis_pipeline(pipeline_cls, True, _parse_rediscluster_command)


class RedisIntegration(Integration):
    identifier = "redis"

    def __init__(self, max_data_size=_DEFAULT_MAX_DATA_SIZE):
        # type: (int) -> None
        self.max_data_size = max_data_size

    @staticmethod
    def setup_once():
        # type: () -> None
        try:
            from redis import StrictRedis, client
        except ImportError:
            raise DidNotEnable("Redis client not installed")

        _patch_redis(StrictRedis, client)
        _patch_rb()

        try:
            _patch_rediscluster()
        except Exception:
            logger.exception("Error occurred while patching `rediscluster` library")


def _get_span_description(name, *args):
    # type: (str, *Any) -> str
    description = name

    with capture_internal_exceptions():
        description = _get_safe_command(name, args)

    return description


def _set_client_data(span, is_cluster, name, *args):
    # type: (Span, bool, str, *Any) -> None
    span.set_data(SPANDATA.DB_SYSTEM, "redis")
    span.set_tag("redis.is_cluster", is_cluster)
    if name:
        span.set_tag("redis.command", name)
        span.set_tag(SPANDATA.DB_OPERATION, name)

    if name and args:
        name_low = name.lower()
        if (name_low in _SINGLE_KEY_COMMANDS) or (
            name_low in _MULTI_KEY_COMMANDS and len(args) == 1
        ):
            span.set_tag("redis.key", args[0])


def patch_redis_client(cls, is_cluster):
    # type: (Any, bool) -> None
    """
    This function can be used to instrument custom redis client classes or
    subclasses.
    """
    old_execute_command = cls.execute_command

    def sentry_patched_execute_command(self, name, *args, **kwargs):
        # type: (Any, str, *Any, **Any) -> Any
        hub = Hub.current
        integration = hub.get_integration(RedisIntegration)

        if integration is None:
            return old_execute_command(self, name, *args, **kwargs)

        description = _get_span_description(name, *args)

        data_should_be_truncated = (
            integration.max_data_size and len(description) > integration.max_data_size
        )
        if data_should_be_truncated:
            description = description[: integration.max_data_size - len("...")] + "..."

        with hub.start_span(op=OP.DB_REDIS, description=description) as span:
            _set_client_data(span, is_cluster, name, *args)

            return old_execute_command(self, name, *args, **kwargs)

    cls.execute_command = sentry_patched_execute_command

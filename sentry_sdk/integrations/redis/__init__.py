from sentry_sdk import Hub
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    logger,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Dict, Sequence
    from redis import Redis, RedisCluster
    from redis.asyncio.cluster import (
        RedisCluster as AsyncRedisCluster,
        ClusterPipeline as AsyncClusterPipeline,
    )
    from sentry_sdk.tracing import Span

_SINGLE_KEY_COMMANDS = frozenset(
    ["decr", "decrby", "get", "incr", "incrby", "pttl", "set", "setex", "setnx", "ttl"],
)
_MULTI_KEY_COMMANDS = frozenset(
    ["del", "touch", "unlink"],
)
_COMMANDS_INCLUDING_SENSITIVE_DATA = [
    "auth",
]
_MAX_NUM_ARGS = 10  # Trim argument lists to this many values
_MAX_NUM_COMMANDS = 10  # Trim command lists to this many values
_DEFAULT_MAX_DATA_SIZE = 1024


def _get_safe_command(name: str, args: Sequence[Any]) -> str:
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


def _get_span_description(name: str, *args: Any) -> str:
    description = name

    with capture_internal_exceptions():
        description = _get_safe_command(name, args)

    return description


def _get_redis_command_args(command: Any) -> Sequence[Any]:
    return command[0]


def _parse_rediscluster_command(command: Any) -> Sequence[Any]:
    return command.args


def _set_pipeline_data(
    span: Span,
    is_cluster: bool,
    get_command_args_fn: Any,
    is_transaction: bool,
    command_stack: Sequence[Any],
) -> None:
    span.set_tag("redis.is_cluster", is_cluster)
    span.set_tag("redis.transaction", is_transaction)

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


def _set_client_data(span: Span, is_cluster: bool, name: str, *args: Any) -> None:
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


def _set_db_data_on_span(span: Span, connection_params: Dict[str, Any]) -> None:
    span.set_data(SPANDATA.DB_SYSTEM, "redis")

    db = connection_params.get("db")
    if db is not None:
        span.set_data(SPANDATA.DB_NAME, str(db))

    host = connection_params.get("host")
    if host is not None:
        span.set_data(SPANDATA.SERVER_ADDRESS, host)

    port = connection_params.get("port")
    if port is not None:
        span.set_data(SPANDATA.SERVER_PORT, port)


def _set_db_data(span: Span, redis_instance: Redis[Any]) -> None:
    try:
        _set_db_data_on_span(span, redis_instance.connection_pool.connection_kwargs)
    except AttributeError:
        pass  # connections_kwargs may be missing in some cases


def _set_cluster_db_data(span: Span, redis_cluster_instance: RedisCluster[Any]) -> None:
    default_node = redis_cluster_instance.get_default_node()
    if default_node is not None:
        _set_db_data_on_span(
            span, {"host": default_node.host, "port": default_node.port}
        )


def _set_async_cluster_db_data(
    span: Span, async_redis_cluster_instance: AsyncRedisCluster[Any]
) -> None:
    default_node = async_redis_cluster_instance.get_default_node()
    if default_node is not None and default_node.connection_kwargs is not None:
        _set_db_data_on_span(span, default_node.connection_kwargs)


def _set_async_cluster_pipeline_db_data(
    span: Span, async_redis_cluster_pipeline_instance: AsyncClusterPipeline[Any]
) -> None:
    with capture_internal_exceptions():
        _set_async_cluster_db_data(
            span,
            # the AsyncClusterPipeline has always had a `_client` attr but it is private so potentially problematic and mypy
            # does not recognize it - see https://github.com/redis/redis-py/blame/v5.0.0/redis/asyncio/cluster.py#L1386
            async_redis_cluster_pipeline_instance._client,  # type: ignore[attr-defined]
        )


def patch_redis_pipeline(
    pipeline_cls: Any,
    is_cluster: bool,
    get_command_args_fn: Any,
    set_db_data_fn: Callable[[Span, Any], None],
) -> None:
    old_execute = pipeline_cls.execute

    def sentry_patched_execute(self: Any, *args: Any, **kwargs: Any) -> Any:
        hub = Hub.current

        if hub.get_integration(RedisIntegration) is None:
            return old_execute(self, *args, **kwargs)

        with hub.start_span(
            op=OP.DB_REDIS, description="redis.pipeline.execute"
        ) as span:
            with capture_internal_exceptions():
                set_db_data_fn(span, self)
                _set_pipeline_data(
                    span,
                    is_cluster,
                    get_command_args_fn,
                    False if is_cluster else self.transaction,
                    self.command_stack,
                )

            return old_execute(self, *args, **kwargs)

    pipeline_cls.execute = sentry_patched_execute


def patch_redis_client(
    cls: Any, is_cluster: bool, set_db_data_fn: Callable[[Span, Any], None]
) -> None:
    """
    This function can be used to instrument custom redis client classes or
    subclasses.
    """
    old_execute_command = cls.execute_command

    def sentry_patched_execute_command(
        self: Any, name: str, *args: Any, **kwargs: Any
    ) -> Any:
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
            set_db_data_fn(span, self)
            _set_client_data(span, is_cluster, name, *args)

            return old_execute_command(self, name, *args, **kwargs)

    cls.execute_command = sentry_patched_execute_command


def _patch_redis(StrictRedis: Any, client: Any) -> None:  # noqa: N803
    patch_redis_client(StrictRedis, is_cluster=False, set_db_data_fn=_set_db_data)
    patch_redis_pipeline(client.Pipeline, False, _get_redis_command_args, _set_db_data)
    try:
        strict_pipeline = client.StrictPipeline
    except AttributeError:
        pass
    else:
        patch_redis_pipeline(
            strict_pipeline, False, _get_redis_command_args, _set_db_data
        )

    try:
        import redis.asyncio
    except ImportError:
        pass
    else:
        from sentry_sdk.integrations.redis.asyncio import (
            patch_redis_async_client,
            patch_redis_async_pipeline,
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


def _patch_redis_cluster() -> None:
    """Patches the cluster module on redis SDK (as opposed to rediscluster library)"""
    try:
        from redis import RedisCluster, cluster
    except ImportError:
        pass
    else:
        patch_redis_client(RedisCluster, True, _set_cluster_db_data)
        patch_redis_pipeline(
            cluster.ClusterPipeline,
            True,
            _parse_rediscluster_command,
            _set_cluster_db_data,
        )

    try:
        from redis.asyncio import cluster as async_cluster
    except ImportError:
        pass
    else:
        from sentry_sdk.integrations.redis.asyncio import (
            patch_redis_async_client,
            patch_redis_async_pipeline,
        )

        patch_redis_async_client(
            async_cluster.RedisCluster,
            is_cluster=True,
            set_db_data_fn=_set_async_cluster_db_data,
        )
        patch_redis_async_pipeline(
            async_cluster.ClusterPipeline,
            True,
            _parse_rediscluster_command,
            set_db_data_fn=_set_async_cluster_pipeline_db_data,
        )


def _patch_rb() -> None:
    try:
        import rb.clients  # type: ignore
    except ImportError:
        pass
    else:
        patch_redis_client(
            rb.clients.FanoutClient, is_cluster=False, set_db_data_fn=_set_db_data
        )
        patch_redis_client(
            rb.clients.MappingClient, is_cluster=False, set_db_data_fn=_set_db_data
        )
        patch_redis_client(
            rb.clients.RoutingClient, is_cluster=False, set_db_data_fn=_set_db_data
        )


def _patch_rediscluster() -> None:
    try:
        import rediscluster  # type: ignore
    except ImportError:
        return

    patch_redis_client(
        rediscluster.RedisCluster, is_cluster=True, set_db_data_fn=_set_db_data
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
        )
    else:
        pipeline_cls = rediscluster.pipeline.ClusterPipeline

    patch_redis_pipeline(
        pipeline_cls, True, _parse_rediscluster_command, set_db_data_fn=_set_db_data
    )


class RedisIntegration(Integration):
    identifier = "redis"

    def __init__(self, max_data_size: int = _DEFAULT_MAX_DATA_SIZE) -> None:
        self.max_data_size = max_data_size

    @staticmethod
    def setup_once() -> None:
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

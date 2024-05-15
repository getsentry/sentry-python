from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.redis.utils import (
    _get_span_description,
    _set_client_data,
    _set_pipeline_data,
)
from sentry_sdk.tracing import Span
from sentry_sdk.utils import SENSITIVE_DATA_SUBSTITUTE, capture_internal_exceptions
import sentry_sdk

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional


def _get_cache_span_description(method_name, args, kwargs):
    # type: (str, list[Any], dict[str, Any]) -> str
    description = "{} {}".format(
        method_name,
        _get_key(method_name, args, kwargs),
    )
    return description


def _get_key(name, args, kwargs):
    # type: (str, list[Any], dict[str, Any]) -> str
    key = ""

    if args is not None and len(args) >= 1:
        key = args[0]
    elif kwargs is not None and "key" in kwargs:
        key = kwargs["key"]

    if isinstance(key, dict):
        # Do not leak sensitive data
        # `set_many()` has a dict {"key1": "value1", "key2": "value2"} as first argument.
        # Those values could include sensitive data so we replace them with a placeholder
        key = {x: SENSITIVE_DATA_SUBSTITUTE for x in key}

    return str(key)


def _get_op(name):
    # type: (str) -> Optional[str]
    op = None
    if name.lower().startswith("get"):
        op = OP.CACHE_GET_ITEM
    elif name.lower().startswith("set"):
        op = OP.CACHE_SET_ITEM

    return op


def patch_redis_pipeline(pipeline_cls, is_cluster, get_command_args_fn, set_db_data_fn):
    # type: (Any, bool, Any, Callable[[Span, Any], None]) -> None
    old_execute = pipeline_cls.execute

    from sentry_sdk.integrations.redis import RedisIntegration

    def sentry_patched_execute(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        if sentry_sdk.get_client().get_integration(RedisIntegration) is None:
            return old_execute(self, *args, **kwargs)

        with sentry_sdk.start_span(
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


def patch_redis_client(cls, is_cluster, set_db_data_fn):
    # type: (Any, bool, Callable[[Span, Any], None]) -> None
    """
    This function can be used to instrument custom redis client classes or
    subclasses.
    """
    old_execute_command = cls.execute_command

    from sentry_sdk.integrations.redis import RedisIntegration

    def sentry_patched_execute_command(self, name, *args, **kwargs):
        # type: (Any, str, *Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(RedisIntegration)
        if integration is None:
            return old_execute_command(self, name, *args, **kwargs)

        description = _get_span_description(name, *args)

        data_should_be_truncated = (
            integration.max_data_size and len(description) > integration.max_data_size
        )
        if data_should_be_truncated:
            description = description[: integration.max_data_size - len("...")] + "..."

        op = _get_op(name)
        key = _get_key(name, args, kwargs)
        description = _get_cache_span_description(name, args, kwargs)

        is_cache_key = False
        for prefix in integration.cache_prefixes:
            if key.startswith(prefix):
                is_cache_key = True
                break

        cache_span = None
        if is_cache_key and op is not None:
            cache_span = sentry_sdk.start_span(op=op, description=description)
            cache_span.__enter__()

        db_span = sentry_sdk.start_span(op=OP.DB_REDIS, description=description)
        db_span.__enter__()

        set_db_data_fn(db_span, self)
        _set_client_data(db_span, is_cluster, name, *args)

        value = old_execute_command(self, name, *args, **kwargs)

        db_span.__exit__(None, None, None)

        if cache_span:
            # .. add len(value) as cache.item_size to outer span
            # .. add more data to cache key from https://develop.sentry.dev/sdk/performance/modules/caches/
            cache_span.set_data(SPANDATA.NETWORK_PEER_ADDRESS, "TODO! localhost")
            cache_span.set_data(SPANDATA.NETWORK_PEER_PORT, 0000)
            cache_span.set_data(SPANDATA.CACHE_KEY, key)
            cache_span.set_data(SPANDATA.CACHE_HIT, True)
            cache_span.set_data(SPANDATA.CACHE_ITEM_SIZE, 000)

            cache_span.__exit__(None, None, None)

        return value

    cls.execute_command = sentry_patched_execute_command

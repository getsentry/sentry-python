from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.redis.utils import (
    _get_span_description,
    _set_client_data,
    _set_pipeline_data,
)
from sentry_sdk.tracing import Span
from sentry_sdk.utils import capture_internal_exceptions, ensure_integration_enabled
import sentry_sdk

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


def patch_redis_pipeline(pipeline_cls, is_cluster, get_command_args_fn, set_db_data_fn):
    # type: (Any, bool, Any, Callable[[Span, Any], None]) -> None
    old_execute = pipeline_cls.execute

    @ensure_integration_enabled(RedisIntegration, old_execute)
    def sentry_patched_execute(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
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

    @ensure_integration_enabled(RedisIntegration, old_execute_command)
    def sentry_patched_execute_command(self, name, *args, **kwargs):
        # type: (Any, str, *Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(RedisIntegration)
        description = _get_span_description(name, *args)

        data_should_be_truncated = (
            integration.max_data_size and len(description) > integration.max_data_size
        )
        if data_should_be_truncated:
            description = description[: integration.max_data_size - len("...")] + "..."

        # TODO: Here we could also create the caching spans.
        # TODO: also need to check if the `name` (if this is the cache key value) matches the prefix we want to configure in __init__ of the integration
        #       Questions:
        #       -) We should probablby have the OP.DB_REDIS span and a separate OP.CACHE_GET_ITEM (or set_item) span, right?
        #       -) We probably need to research what redis commands are used by caching libs.
        # GitHub issue: https://github.com/getsentry/sentry-python/issues/2965
        with sentry_sdk.start_span(op=OP.DB_REDIS, description=description) as span:
            set_db_data_fn(span, self)
            _set_client_data(span, is_cluster, name, *args)

            return old_execute_command(self, name, *args, **kwargs)

    cls.execute_command = sentry_patched_execute_command

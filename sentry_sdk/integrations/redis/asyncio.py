from __future__ import absolute_import

from sentry_sdk import Hub
from sentry_sdk.consts import OP
from sentry_sdk.integrations.redis import (
    RedisIntegration,
    _get_redis_command_args,
    _get_span_description,
    _set_client_data,
    _set_db_data,
    _set_pipeline_data,
)
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any


def patch_redis_async_pipeline(pipeline_cls):
    # type: (Any) -> None
    old_execute = pipeline_cls.execute

    async def _sentry_execute(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        hub = Hub.current

        if hub.get_integration(RedisIntegration) is None:
            return await old_execute(self, *args, **kwargs)

        with hub.start_span(
            op=OP.DB_REDIS, description="redis.pipeline.execute"
        ) as span:
            with capture_internal_exceptions():
                _set_db_data(span, self.connection_pool.connection_kwargs)
                _set_pipeline_data(
                    span,
                    False,
                    _get_redis_command_args,
                    self.is_transaction,
                    self.command_stack,
                )

            return await old_execute(self, *args, **kwargs)

    pipeline_cls.execute = _sentry_execute


def patch_redis_async_client(cls):
    # type: (Any) -> None
    old_execute_command = cls.execute_command

    async def _sentry_execute_command(self, name, *args, **kwargs):
        # type: (Any, str, *Any, **Any) -> Any
        hub = Hub.current

        if hub.get_integration(RedisIntegration) is None:
            return await old_execute_command(self, name, *args, **kwargs)

        description = _get_span_description(name, *args)

        with hub.start_span(op=OP.DB_REDIS, description=description) as span:
            _set_db_data(span, self.connection_pool.connection_kwargs)
            _set_client_data(span, False, name, *args)

            return await old_execute_command(self, name, *args, **kwargs)

    cls.execute_command = _sentry_execute_command

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.redis.consts import SPAN_ORIGIN
from sentry_sdk.integrations.redis.modules.caches import (
    _compile_cache_span_properties,
    _set_cache_data,
)
from sentry_sdk.integrations.redis.modules.queries import _compile_db_span_properties
from sentry_sdk.integrations.redis.utils import (
    _set_client_data,
    _set_pipeline_data,
)
from sentry_sdk.tracing import Span
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import capture_internal_exceptions

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional, Union
    from sentry_sdk.traces import StreamedSpan


def patch_redis_pipeline(
    pipeline_cls: "Any",
    is_cluster: bool,
    get_command_args_fn: "Any",
    set_db_data_fn: "Callable[[Union[Span, StreamedSpan], Any], None]",
) -> None:
    old_execute = pipeline_cls.execute

    from sentry_sdk.integrations.redis import RedisIntegration

    def sentry_patched_execute(self: "Any", *args: "Any", **kwargs: "Any") -> "Any":
        client = sentry_sdk.get_client()
        if client.get_integration(RedisIntegration) is None:
            return old_execute(self, *args, **kwargs)

        span_streaming = has_span_streaming_enabled(client.options)

        span: "Union[Span, StreamedSpan]"
        if span_streaming:
            span = sentry_sdk.traces.start_span(name="redis.pipeline.execute")
            span.set_origin(SPAN_ORIGIN)
            span.set_op(OP.DB_REDIS)
        else:
            span = sentry_sdk.start_span(
                op=OP.DB_REDIS,
                name="redis.pipeline.execute",
                origin=SPAN_ORIGIN,
            )

        with span:
            with capture_internal_exceptions():
                command_seq = None
                try:
                    command_seq = self._execution_strategy.command_queue
                except AttributeError:
                    command_seq = self.command_stack

                set_db_data_fn(span, self)
                _set_pipeline_data(
                    span,
                    is_cluster,
                    get_command_args_fn,
                    False if is_cluster else self.transaction,
                    command_seq,
                )

            return old_execute(self, *args, **kwargs)

    pipeline_cls.execute = sentry_patched_execute


def patch_redis_client(
    cls: "Any",
    is_cluster: bool,
    set_db_data_fn: "Callable[[Union[Span, StreamedSpan], Any], None]",
) -> None:
    """
    This function can be used to instrument custom redis client classes or
    subclasses.
    """
    old_execute_command = cls.execute_command

    from sentry_sdk.integrations.redis import RedisIntegration

    def sentry_patched_execute_command(
        self: "Any", name: str, *args: "Any", **kwargs: "Any"
    ) -> "Any":
        client = sentry_sdk.get_client()
        integration = client.get_integration(RedisIntegration)
        if integration is None:
            return old_execute_command(self, name, *args, **kwargs)

        span_streaming = has_span_streaming_enabled(client.options)

        cache_properties = _compile_cache_span_properties(
            name,
            args,
            kwargs,
            integration,
        )

        cache_span: "Optional[Union[Span, StreamedSpan]]" = None
        if cache_properties["is_cache_key"] and cache_properties["op"] is not None:
            if span_streaming:
                cache_span = sentry_sdk.traces.start_span(
                    name=cache_properties["description"]
                )
                cache_span.set_op(cache_properties["op"])
                cache_span.set_origin(SPAN_ORIGIN)
            else:
                cache_span = sentry_sdk.start_span(
                    op=cache_properties["op"],
                    name=cache_properties["description"],
                    origin=SPAN_ORIGIN,
                )
            cache_span.__enter__()

        db_properties = _compile_db_span_properties(integration, name, args)

        db_span: "Union[Span, StreamedSpan]"
        if span_streaming:
            db_span = sentry_sdk.traces.start_span(name=db_properties["description"])
            db_span.set_op(db_properties["op"])
            db_span.set_origin(SPAN_ORIGIN)
        else:
            db_span = sentry_sdk.start_span(
                op=db_properties["op"],
                name=db_properties["description"],
                origin=SPAN_ORIGIN,
            )
        db_span.__enter__()

        with capture_internal_exceptions():
            set_db_data_fn(db_span, self)
            _set_client_data(db_span, is_cluster, name, *args)

        try:
            value = old_execute_command(self, name, *args, **kwargs)
        finally:
            db_span.__exit__(None, None, None)

            if cache_span:
                with capture_internal_exceptions():
                    _set_cache_data(cache_span, self, cache_properties, value)

                cache_span.__exit__(None, None, None)

        return value

    cls.execute_command = sentry_patched_execute_command

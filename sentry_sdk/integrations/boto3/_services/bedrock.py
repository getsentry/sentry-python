import sys
from typing import TYPE_CHECKING, cast

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.traces import SpanStatus, StreamedSpan
from sentry_sdk.tracing import SPANSTATUS
from sentry_sdk.utils import (
    capture_internal_exceptions,
    reraise,
    should_be_treated_as_error,
)

try:
    from botocore.eventstream import EventStream
except ImportError:
    raise DidNotEnable("botocore is not installed")

if TYPE_CHECKING:
    from typing import Any, Iterator, Optional, Union

    from mypy_boto3_bedrock_runtime.type_defs import (
        ConverseResponseTypeDef,
        ConverseStreamResponseTypeDef,
    )

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.tracing import Span


def _finish_span(
    stream: "EventStream[Any]",
    exc_type: "Optional[type[BaseException]]",
    exc_val: "Optional[BaseException]",
    exc_tb: "Optional[Any]",
) -> None:
    """
    Finishes the span attached to the stream.
    Is a no-op if the stream has no span set, i.e., when the span has already been finished.
    """
    if not hasattr(stream, "_span"):
        return

    span = stream._span
    del stream._span

    if should_be_treated_as_error(exc_type, exc_val):
        if isinstance(span, StreamedSpan):
            span.status = SpanStatus.ERROR
        else:
            span.status = SPANSTATUS.INTERNAL_ERROR

    if isinstance(span, StreamedSpan):
        span.end()
    else:
        span.finish()


def _patch_event_stream() -> None:
    original_iter = EventStream.__iter__
    original_close = EventStream.close

    def sentry_patched_iter(
        self: "EventStream[Any]",
    ) -> "Iterator[Any]":
        """
        Sets information received while iterating the response stream on the AI Client Span.
        Responsible for closing the AI Client Span unless the span has already been closed in the close() patch.
        """
        span = getattr(self, "_span", None)
        if span is None:
            yield from original_iter(self)
            return

        set_on_span = (
            span.set_attribute if isinstance(span, StreamedSpan) else span.set_data
        )

        try:
            for event in original_iter(self):
                if "metadata" not in event:
                    yield event
                    continue

                usage = event["metadata"]["usage"]

                input_tokens = usage["inputTokens"]
                set_on_span(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"])
                set_on_span(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"])

                cache_read_tokens = usage.get("cacheReadInputTokens")
                if cache_read_tokens is not None:
                    input_tokens += cache_read_tokens
                    set_on_span(
                        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                    )

                cache_write_tokens = usage.get("cacheWriteInputTokens")
                if cache_write_tokens is not None:
                    input_tokens += cache_write_tokens
                    set_on_span(
                        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE,
                        cache_write_tokens,
                    )

                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS,
                    input_tokens,
                )

                yield event
        except GeneratorExit:
            raise  # Leave span open since underlying stream is not necessarily closed.
        except BaseException:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _finish_span(self, *exc_info)
            reraise(*exc_info)

        _finish_span(self, None, None, None)

    def sentry_patched_close(self: "EventStream[Any]") -> None:
        """
        Closes the AI Client Span unless `sentry_patched_iter()` closes the span first.
        """
        try:
            original_close(self)
        except BaseException:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _finish_span(self, *exc_info)
            reraise(*exc_info)

        _finish_span(self, None, None, None)

    EventStream.__iter__ = sentry_patched_iter  # type: ignore[method-assign]
    EventStream.close = sentry_patched_close  # type: ignore[method-assign]


class _BedrockExtension(_ServiceExtension):
    __slots__ = ()
    skip_if_no_active_span = False

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name != "Converse" and ctx.operation_name != "ConverseStream":
            return None
        return OP.GEN_AI_CHAT

    def get_span_origin(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name != "Converse" and ctx.operation_name != "ConverseStream":
            return None
        return "auto.ai.boto3"

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        if ctx.operation_name != "Converse" and ctx.operation_name != "ConverseStream":
            return {}

        return {
            SPANDATA.GEN_AI_PROVIDER_NAME: "aws.bedrock",
            SPANDATA.GEN_AI_OPERATION_NAME: "chat",
            SPANDATA.GEN_AI_RESPONSE_STREAMING: ctx.operation_name == "ConverseStream",
        }

    def on_success_response(
        self,
        ctx: "AwsCallContext",
        response: "Any",
        span: "Union[Span, StreamedSpan]",
    ) -> None:
        set_on_span = (
            span.set_attribute if isinstance(span, StreamedSpan) else span.set_data
        )

        if ctx.operation_name == "Converse":
            usage = cast("ConverseResponseTypeDef", response)["usage"]

            set_on_span(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"])
            set_on_span(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"])

            input_tokens = usage["inputTokens"]

            cache_read_tokens = usage.get("cacheReadInputTokens")
            if cache_read_tokens is not None:
                input_tokens += cache_read_tokens
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                )

            cache_write_tokens = usage.get("cacheWriteInputTokens")
            if cache_write_tokens is not None:
                input_tokens += cache_write_tokens
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE, cache_write_tokens
                )

            set_on_span(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)

            if isinstance(span, StreamedSpan):
                span.end()
                return

            span.finish()
            return

        if ctx.operation_name == "ConverseStream":
            stream = cast("ConverseStreamResponseTypeDef", response)["stream"]
            stream._span = span  # type: ignore[attr-defined]
            return

        if isinstance(span, StreamedSpan):
            span.end()
            return

        span.finish()

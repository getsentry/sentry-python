import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.traces import SpanStatus, StreamedSpan
from sentry_sdk.tracing import SPANSTATUS
from sentry_sdk.utils import capture_internal_exceptions, reraise

if TYPE_CHECKING:
    from typing import Any, Iterator, Optional, Union

    from botocore.eventstream import EventStream
    from mypy_boto3_bedrock_runtime.type_defs import (
        ConverseResponseTypeDef,
        ConverseStreamOutputTypeDef,
        ConverseStreamResponseTypeDef,
    )

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.tracing import Span


@contextmanager
def _stream_span_context(span: "Union[Span, StreamedSpan]"):
    """
    Closes the provided span and set the span's status on exit.
    """
    end_span = span.end if isinstance(span, StreamedSpan) else span.finish

    try:
        yield
    except GeneratorExit:  # Raised when close() is called on the stream.
        exc_info = sys.exc_info()
        with capture_internal_exceptions():
            end_span()
        reraise(*exc_info)
    except BaseException:
        exc_info = sys.exc_info()
        with capture_internal_exceptions():
            if isinstance(span, StreamedSpan):
                span.status = SpanStatus.ERROR
            else:
                span.status = SPANSTATUS.INTERNAL_ERROR
            end_span()
        reraise(*exc_info)
    else:
        end_span()


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

            set_on_span(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage["inputTokens"])
            set_on_span(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"])
            set_on_span(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"])

            cache_read_tokens = usage.get("cacheReadInputTokens")
            if cache_read_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                )

            cache_write_tokens = usage.get("cacheWriteInputTokens")
            if cache_write_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE, cache_write_tokens
                )

            if isinstance(span, StreamedSpan):
                span.end()
                return

            span.finish()
            return

        if ctx.operation_name != "ConverseStream":
            return

        stream = cast("ConverseStreamResponseTypeDef", response)["stream"]
        response["stream"] = _wrap_converse_stream(stream, span)


def _wrap_converse_stream(
    stream: "EventStream[ConverseStreamOutputTypeDef]",
    span: "Union[Span, StreamedSpan]",
) -> "Iterator[ConverseStreamOutputTypeDef]":
    set_on_span = (
        span.set_attribute if isinstance(span, StreamedSpan) else span.set_data
    )

    with _stream_span_context(span):
        for event in stream:
            if "metadata" not in event:
                yield event
                continue

            usage = event["metadata"]["usage"]

            set_on_span(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage["inputTokens"])
            set_on_span(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"])
            set_on_span(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"])

            cache_read_tokens = usage.get("cacheReadInputTokens")
            if cache_read_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                )

            cache_write_tokens = usage.get("cacheWriteInputTokens")
            if cache_write_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE, cache_write_tokens
                )

            yield event

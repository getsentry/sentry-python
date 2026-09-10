from typing import TYPE_CHECKING

from sentry_sdk.ai.utils import _set_span_data_attribute
from sentry_sdk.consts import SPANDATA
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import Span

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable, Optional, TypeVar, Union

    F = TypeVar("F", bound=Union[Callable[..., Any], Callable[..., Awaitable[Any]]])


def record_token_usage(
    span: "Union[Span, StreamedSpan]",
    input_tokens: "Optional[int]" = None,
    input_tokens_cached: "Optional[int]" = None,
    input_tokens_cache_write: "Optional[int]" = None,
    output_tokens: "Optional[int]" = None,
    output_tokens_reasoning: "Optional[int]" = None,
    total_tokens: "Optional[int]" = None,
) -> None:
    if input_tokens is not None:
        _set_span_data_attribute(span, SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)

    if input_tokens_cached is not None:
        _set_span_data_attribute(
            span,
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
            input_tokens_cached,
        )

    if input_tokens_cache_write is not None:
        _set_span_data_attribute(
            span,
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE,
            input_tokens_cache_write,
        )

    if output_tokens is not None:
        _set_span_data_attribute(
            span, SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens
        )

    if output_tokens_reasoning is not None:
        _set_span_data_attribute(
            span,
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
            output_tokens_reasoning,
        )

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    if total_tokens is not None:
        _set_span_data_attribute(span, SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, total_tokens)

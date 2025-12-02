"""Utility functions for PydanticAI span instrumentation."""

import sentry_sdk
from sentry_sdk.consts import SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union
    from pydantic_ai.usage import RequestUsage, RunUsage  # type: ignore


def _set_usage_data(span, usage):
    # type: (sentry_sdk.tracing.Span, Union[RequestUsage, RunUsage]) -> None
    """Set token usage data on a span.

    This function works with both RequestUsage (single request) and
    RunUsage (agent run) objects from pydantic_ai.

    Args:
        span: The Sentry span to set data on.
        usage: RequestUsage or RunUsage object containing token usage information.
    """
    if usage is None:
        return

    if hasattr(usage, "input_tokens") and usage.input_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens)

    if hasattr(usage, "output_tokens") and usage.output_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens)

    if hasattr(usage, "total_tokens") and usage.total_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_tokens)

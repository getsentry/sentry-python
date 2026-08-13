"""Utility functions for PydanticAI span instrumentation."""

from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.monitoring import record_token_usage

from .._extract import extract_usage_kwargs

if TYPE_CHECKING:
    from typing import Union

    from pydantic_ai.usage import RequestUsage, RunUsage

    from sentry_sdk.traces import StreamedSpan


def _set_usage_data(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    usage: "Union[RequestUsage, RunUsage]",
) -> None:
    """Set token usage data on a span.

    This function works with both RequestUsage (single request) and
    RunUsage (agent run) objects from pydantic_ai.

    Args:
        span: The Sentry span to set data on.
        usage: RequestUsage or RunUsage object containing token usage information.
    """
    usage_kwargs = extract_usage_kwargs(usage)
    if usage_kwargs is None:
        return

    record_token_usage(span, **usage_kwargs)

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

from sentry_sdk.tracing import Span
from sentry_sdk.utils import logger


def _normalize_data(data):
    # type: (Any) -> Any

    # convert pydantic data (e.g. OpenAI v1+) to json compatible format
    if hasattr(data, "model_dump"):
        try:
            return data.model_dump()
        except Exception as e:
            logger.warning("Could not convert pydantic data to JSON: %s", e)
            return data
    if isinstance(data, list):
        if len(data) == 1:
            return _normalize_data(data[0])  # remove empty dimensions
        return list(_normalize_data(x) for x in data)
    if isinstance(data, dict):
        return {k: _normalize_data(v) for (k, v) in data.items()}
    return data


def set_data_normalized(span, key, value):
    # type: (Span, str, Any) -> None
    normalized = _normalize_data(value)
    span.set_data(key, normalized)


def record_token_usage(
    span, prompt_tokens=None, completion_tokens=None, total_tokens=None
):
    # type: (Span, Optional[int], Optional[int], Optional[int]) -> None
    if prompt_tokens is not None:
        span.set_measurement("ai_prompt_tokens_used", value=prompt_tokens)
    if completion_tokens is not None:
        span.set_measurement("ai_completion_tokens_used", value=completion_tokens)
    if (
        total_tokens is None
        and prompt_tokens is not None
        and completion_tokens is not None
    ):
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens is not None:
        span.set_measurement("ai_total_tokens_used", total_tokens)

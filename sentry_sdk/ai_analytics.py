from functools import wraps

from sentry_sdk import start_span
from sentry_sdk.tracing import Span
from sentry_sdk.utils import ContextVar
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Callable, Any

_ai_pipeline_name = ContextVar("ai_pipeline_name", default=None)


def set_ai_pipeline_name(name):
    # type: (Optional[str]) -> None
    _ai_pipeline_name.set(name)


def get_ai_pipeline_name():
    # type: () -> Optional[str]
    return _ai_pipeline_name.get()


def ai_pipeline(description, op="ai.pipeline", **span_kwargs):
    # type: (str, str, Any) -> Callable[..., Any]
    def decorator(f):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        @wraps(f)
        def wrapped(*args, **kwargs):
            # type: (Any, Any) -> Any
            with start_span(description=description, op=op, **span_kwargs):
                _ai_pipeline_name.set(description)
                res = f(*args, **kwargs)
                _ai_pipeline_name.set(None)
                return res

        return wrapped

    return decorator


def ai_run(description, op="ai.run", **span_kwargs):
    # type: (str, str, Any) -> Callable[..., Any]
    def decorator(f):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        @wraps(f)
        def wrapped(*args, **kwargs):
            # type: (Any, Any) -> Any
            with start_span(description=description, op=op, **span_kwargs) as span:
                curr_pipeline = _ai_pipeline_name.get()
                if curr_pipeline:
                    span.set_data("ai.pipeline.name", curr_pipeline)
                return f(*args, **kwargs)

        return wrapped

    return decorator


def record_token_usage(
    span, prompt_tokens=None, completion_tokens=None, total_tokens=None
):
    # type: (Span, Optional[int], Optional[int], Optional[int]) -> None
    ai_pipeline_name = get_ai_pipeline_name()
    if ai_pipeline_name:
        span.set_data("ai.pipeline.name", ai_pipeline_name)
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

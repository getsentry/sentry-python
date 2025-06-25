from __future__ import annotations
import inspect
from functools import wraps

from sentry_sdk.consts import SPANDATA
import sentry_sdk.utils
from sentry_sdk import start_span
from sentry_sdk.tracing import Span
from sentry_sdk.utils import ContextVar

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Callable, Any

_ai_pipeline_name = ContextVar("ai_pipeline_name", default=None)


def set_ai_pipeline_name(name: Optional[str]) -> None:
    _ai_pipeline_name.set(name)


def get_ai_pipeline_name() -> Optional[str]:
    return _ai_pipeline_name.get()


def ai_track(description: str, **span_kwargs: Any) -> Callable[..., Any]:
    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
            curr_pipeline = _ai_pipeline_name.get()
            op = span_kwargs.get("op", "ai.run" if curr_pipeline else "ai.pipeline")

            with start_span(
                name=description, op=op, only_if_parent=True, **span_kwargs
            ) as span:
                for k, v in kwargs.pop("sentry_tags", {}).items():
                    span.set_tag(k, v)
                for k, v in kwargs.pop("sentry_data", {}).items():
                    span.set_attribute(k, v)
                if curr_pipeline:
                    span.set_attribute(SPANDATA.AI_PIPELINE_NAME, curr_pipeline)
                    return f(*args, **kwargs)
                else:
                    _ai_pipeline_name.set(description)
                    try:
                        res = f(*args, **kwargs)
                    except Exception as e:
                        event, hint = sentry_sdk.utils.event_from_exception(
                            e,
                            client_options=sentry_sdk.get_client().options,
                            mechanism={"type": "ai_monitoring", "handled": False},
                        )
                        sentry_sdk.capture_event(event, hint=hint)
                        raise e from None
                    finally:
                        _ai_pipeline_name.set(None)
                    return res

        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            curr_pipeline = _ai_pipeline_name.get()
            op = span_kwargs.get("op", "ai.run" if curr_pipeline else "ai.pipeline")

            with start_span(
                name=description, op=op, only_if_parent=True, **span_kwargs
            ) as span:
                for k, v in kwargs.pop("sentry_tags", {}).items():
                    span.set_tag(k, v)
                for k, v in kwargs.pop("sentry_data", {}).items():
                    span.set_attribute(k, v)
                if curr_pipeline:
                    span.set_attribute(SPANDATA.AI_PIPELINE_NAME, curr_pipeline)
                    return await f(*args, **kwargs)
                else:
                    _ai_pipeline_name.set(description)
                    try:
                        res = await f(*args, **kwargs)
                    except Exception as e:
                        event, hint = sentry_sdk.utils.event_from_exception(
                            e,
                            client_options=sentry_sdk.get_client().options,
                            mechanism={"type": "ai_monitoring", "handled": False},
                        )
                        sentry_sdk.capture_event(event, hint=hint)
                        raise e from None
                    finally:
                        _ai_pipeline_name.set(None)
                    return res

        if inspect.iscoroutinefunction(f):
            return wraps(f)(async_wrapped)
        else:
            return wraps(f)(sync_wrapped)

    return decorator


def record_token_usage(
    span: Span,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    ai_pipeline_name = get_ai_pipeline_name()
    if ai_pipeline_name:
        span.set_attribute(SPANDATA.AI_PIPELINE_NAME, ai_pipeline_name)
    if prompt_tokens is not None:
        span.set_attribute(SPANDATA.AI_PROMPT_TOKENS_USED, prompt_tokens)
    if completion_tokens is not None:
        span.set_attribute(SPANDATA.AI_COMPLETION_TOKENS_USED, completion_tokens)
    if (
        total_tokens is None
        and prompt_tokens is not None
        and completion_tokens is not None
    ):
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens is not None:
        span.set_attribute(SPANDATA.AI_TOTAL_TOKENS_USED, total_tokens)

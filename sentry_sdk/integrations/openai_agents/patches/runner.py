import asyncio

from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from ..spans import (
    agent_workflow_span,
    update_agent_workflow_span,
)
from ..utils import _capture_exception, _wrap_hooks

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable

try:
    import agents
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _create_run_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps the agents.Runner.run* methods to create a root span for the agent workflow runs.
    """
    is_async = asyncio.iscoroutinefunction(original_func)

    @wraps(
        original_func.__func__ if hasattr(original_func, "__func__") else original_func
    )
    async def async_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = args[0]
        with agent_workflow_span(agent) as span:
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            result = None
            try:
                result = await original_func(*args, **kwargs)
                return result
            except Exception as exc:
                _capture_exception(exc)

                # It could be that there is a "invoke agent" span still open
                # (because its create in run_hooks without the context manager)
                current_span = sentry_sdk.get_current_span()
                if current_span is not None and current_span.timestamp is None:
                    current_span.__exit__(None, None, None)

                raise exc from None
            finally:
                update_agent_workflow_span(span, agent, result)

    @wraps(
        original_func.__func__ if hasattr(original_func, "__func__") else original_func
    )
    def sync_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        # The sync version (.run_sync()) is just a wrapper around the async version (.run())
        return original_func(*args, **kwargs)

    return async_wrapper if is_async else sync_wrapper

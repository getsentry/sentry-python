import asyncio

from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import agent_workflow_span, update_agent_workflow_span
from ..utils import _wrap_hooks

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
            result = await original_func(*args, **kwargs)
            update_agent_workflow_span(span, agent, result)

        return result

    @wraps(
        original_func.__func__ if hasattr(original_func, "__func__") else original_func
    )
    def sync_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = args[0]
        with agent_workflow_span(agent) as span:
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            result = original_func(*args, **kwargs)
            update_agent_workflow_span(span, agent, result)

        return result

    return async_wrapper if is_async else sync_wrapper

import asyncio

from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import (
    agent_workflow_span,
    update_agent_workflow_span,
    invoke_agent_span,
    update_invoke_agent_span,
)

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
            result = await original_func(*args, **kwargs)
            update_agent_workflow_span(span, agent, result)

        return result

    @wraps(
        original_func.__func__ if hasattr(original_func, "__func__") else original_func
    )
    def sync_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        # The sync version (.run_sync()) is just a wrapper around the async version (.run())
        return original_func(*args, **kwargs)

    return async_wrapper if is_async else sync_wrapper


def _create_run_single_turn_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps the agents.Runner._run_single_turn method to create a span for the agent invocation.
    """

    @wraps(
        original_func.__func__ if hasattr(original_func, "__func__") else original_func
    )
    async def async_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = kwargs.get("agent")
        if agent is None or not kwargs.get("should_run_agent_start_hooks", False):
            return await original_func(*args, **kwargs)

        with invoke_agent_span(agent) as span:
            result = await original_func(*args, **kwargs)
            update_invoke_agent_span(span, agent, result)

        return result

    return async_wrapper

from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from ..spans import agent_workflow_span, invoke_agent_span, update_invoke_agent_span
from ..utils import _capture_exception

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable

try:
    import pydantic_ai
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


def _create_run_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps the Agent.run method to create a root span for the agent workflow.
    """

    @wraps(original_func)
    async def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Isolate each workflow so that when agents are run in asyncio tasks they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            # Store agent reference in Sentry scope for access in nested spans
            # We store the full agent to allow access to tools and system prompts
            sentry_sdk.get_current_scope().set_context(
                "pydantic_ai_agent", {"_agent": self}
            )

            with agent_workflow_span(self):
                result = None
                try:
                    result = await original_func(self, *args, **kwargs)
                    return result
                except Exception as exc:
                    _capture_exception(exc)

                    # It could be that there is an "invoke agent" span still open
                    current_span = sentry_sdk.get_current_span()
                    if current_span is not None and current_span.timestamp is None:
                        current_span.__exit__(None, None, None)

                    raise exc from None

    return wrapper


def _create_run_sync_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps the Agent.run_sync method to create a root span for the agent workflow.
    """

    @wraps(original_func)
    def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Isolate each workflow so that when agents are run they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            # Store agent reference in Sentry scope for access in nested spans
            # We store the full agent to allow access to tools and system prompts
            sentry_sdk.get_current_scope().set_context(
                "pydantic_ai_agent", {"_agent": self}
            )

            with agent_workflow_span(self):
                result = None
                try:
                    result = original_func(self, *args, **kwargs)
                    return result
                except Exception as exc:
                    _capture_exception(exc)

                    # It could be that there is an "invoke agent" span still open
                    current_span = sentry_sdk.get_current_span()
                    if current_span is not None and current_span.timestamp is None:
                        current_span.__exit__(None, None, None)

                    raise exc from None

    return wrapper


def _patch_agent_run():
    # type: () -> None
    """
    Patches the Agent run methods to create spans for agent execution.
    """
    # Import here to avoid circular imports
    from pydantic_ai.agent import Agent

    # Store original methods
    original_run = Agent.run
    original_run_sync = Agent.run_sync

    # Wrap and apply patches
    Agent.run = _create_run_wrapper(original_run)  # type: ignore
    Agent.run_sync = _create_run_sync_wrapper(original_run_sync)  # type: ignore

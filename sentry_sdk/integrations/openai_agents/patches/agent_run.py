from functools import wraps

from sentry_sdk.integrations import DidNotEnable
from ..spans import invoke_agent_span, update_invoke_agent_span, handoff_span

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

try:
    import agents
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _patch_agent_run():
    # type: () -> None
    """
    Patches AgentRunner methods to create agent invocation spans.
    This directly patches the execution flow to track when agents start and stop.
    """

    # Store original methods
    original_run_single_turn = agents.run.AgentRunner._run_single_turn
    original_execute_handoffs = agents._run_impl.RunImpl.execute_handoffs
    original_execute_final_output = agents._run_impl.RunImpl.execute_final_output

    def _start_invoke_agent_span(context_wrapper, agent):
        # type: (agents.RunContextWrapper, agents.Agent) -> None
        """Start an agent invocation span"""
        # Store the agent on the context wrapper so we can access it later
        context_wrapper._sentry_current_agent = agent
        invoke_agent_span(context_wrapper, agent)

    def _end_invoke_agent_span(context_wrapper, agent, output=None):
        # type: (agents.RunContextWrapper, agents.Agent, Optional[Any]) -> None
        """End the agent invocation span"""
        # Clear the stored agent
        if hasattr(context_wrapper, "_sentry_current_agent"):
            delattr(context_wrapper, "_sentry_current_agent")

        update_invoke_agent_span(context_wrapper, agent, output)

    def _has_active_agent_span(context_wrapper):
        # type: (agents.RunContextWrapper) -> bool
        """Check if there's an active agent span for this context"""
        return getattr(context_wrapper, "_sentry_current_agent", None) is not None

    def _get_current_agent(context_wrapper):
        # type: (agents.RunContextWrapper) -> Optional[agents.Agent]
        """Get the current agent from context wrapper"""
        return getattr(context_wrapper, "_sentry_current_agent", None)

    @wraps(
        original_run_single_turn.__func__
        if hasattr(original_run_single_turn, "__func__")
        else original_run_single_turn
    )
    async def patched_run_single_turn(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        """Patched _run_single_turn that creates agent invocation spans"""
        agent = kwargs.get("agent")
        context_wrapper = kwargs.get("context_wrapper")
        should_run_agent_start_hooks = kwargs.get("should_run_agent_start_hooks")

        # Start agent span when agent starts (but only once per agent)
        if should_run_agent_start_hooks and agent and context_wrapper:
            # End any existing span for a different agent
            if _has_active_agent_span(context_wrapper):
                current_agent = _get_current_agent(context_wrapper)
                if current_agent and current_agent != agent:
                    _end_invoke_agent_span(context_wrapper, current_agent)

            _start_invoke_agent_span(context_wrapper, agent)

        # Call original method with all the correct parameters
        result = await original_run_single_turn(*args, **kwargs)

        return result

    @wraps(
        original_execute_handoffs.__func__
        if hasattr(original_execute_handoffs, "__func__")
        else original_execute_handoffs
    )
    async def patched_execute_handoffs(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        """Patched execute_handoffs that creates handoff spans and ends agent span for handoffs"""

        context_wrapper = kwargs.get("context_wrapper")
        run_handoffs = kwargs.get("run_handoffs")
        agent = kwargs.get("agent")

        # Create Sentry handoff span for the first handoff (agents library only processes the first one)
        if run_handoffs:
            first_handoff = run_handoffs[0]
            handoff_agent_name = first_handoff.handoff.agent_name
            handoff_span(context_wrapper, agent, handoff_agent_name)

        # Call original method with all parameters
        try:
            result = await original_execute_handoffs(*args, **kwargs)

        finally:
            # End span for current agent after handoff processing is complete
            if agent and context_wrapper and _has_active_agent_span(context_wrapper):
                _end_invoke_agent_span(context_wrapper, agent)

        return result

    @wraps(
        original_execute_final_output.__func__
        if hasattr(original_execute_final_output, "__func__")
        else original_execute_final_output
    )
    async def patched_execute_final_output(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        """Patched execute_final_output that ends agent span for final outputs"""

        agent = kwargs.get("agent")
        context_wrapper = kwargs.get("context_wrapper")
        final_output = kwargs.get("final_output")

        # Call original method with all parameters
        try:
            result = await original_execute_final_output(*args, **kwargs)
        finally:
            # End span for current agent after final output processing is complete
            if agent and context_wrapper and _has_active_agent_span(context_wrapper):
                _end_invoke_agent_span(context_wrapper, agent, final_output)

        return result

    # Apply patches
    agents.run.AgentRunner._run_single_turn = classmethod(patched_run_single_turn)
    agents._run_impl.RunImpl.execute_handoffs = classmethod(patched_execute_handoffs)
    agents._run_impl.RunImpl.execute_final_output = classmethod(
        patched_execute_final_output
    )

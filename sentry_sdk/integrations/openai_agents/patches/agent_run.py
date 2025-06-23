from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import invoke_agent_span, update_invoke_agent_span


try:
    import agents
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _patch_agent_run():
    """
    Patches AgentRunner methods to create agent invocation spans without using RunHooks.
    This directly patches the execution flow to track when agents start and stop.
    """

    # Store original methods
    original_run_single_turn = agents.run.AgentRunner._run_single_turn
    original_execute_handoffs = agents._run_impl.RunImpl.execute_handoffs
    original_execute_final_output = agents._run_impl.RunImpl.execute_final_output

    def _start_invoke_agent_span(context_wrapper, agent):
        """Start an agent invocation span"""
        # Store the agent on the context wrapper so we can access it later
        context_wrapper._sentry_current_agent = agent
        invoke_agent_span(context_wrapper, agent)

    def _end_invoke_agent_span(context_wrapper, agent, output):
        """End the agent invocation span"""
        # Clear the stored agent
        if hasattr(context_wrapper, "_sentry_current_agent"):
            delattr(context_wrapper, "_sentry_current_agent")

        update_invoke_agent_span(context_wrapper, agent, output)

    def _has_active_agent_span(context_wrapper):
        """Check if there's an active agent span for this context"""
        return hasattr(context_wrapper, "_sentry_current_agent")

    def _get_current_agent(context_wrapper):
        """Get the current agent from context wrapper"""
        return getattr(context_wrapper, "_sentry_current_agent", None)

    @wraps(original_run_single_turn)
    async def patched_run_single_turn(
        cls,
        *,
        agent,
        all_tools,
        original_input,
        generated_items,
        hooks,
        context_wrapper,
        run_config,
        should_run_agent_start_hooks,
        tool_use_tracker,
        previous_response_id,
    ):
        """Patched _run_single_turn that creates agent invocation spans"""

        # Start agent span when agent starts (but only once per agent)
        if should_run_agent_start_hooks and agent and context_wrapper:
            # End any existing span for a different agent
            if _has_active_agent_span(context_wrapper):
                current_agent = _get_current_agent(context_wrapper)
                if current_agent and current_agent != agent:
                    _end_invoke_agent_span(context_wrapper, current_agent, None)

            _start_invoke_agent_span(context_wrapper, agent)

        # Call original method with all the correct parameters
        result = await original_run_single_turn(
            agent=agent,
            all_tools=all_tools,
            original_input=original_input,
            generated_items=generated_items,
            hooks=hooks,
            context_wrapper=context_wrapper,
            run_config=run_config,
            should_run_agent_start_hooks=should_run_agent_start_hooks,
            tool_use_tracker=tool_use_tracker,
            previous_response_id=previous_response_id,
        )

        return result

    @wraps(original_execute_handoffs)
    async def patched_execute_handoffs(
        cls,
        *,
        agent,
        original_input,
        pre_step_items,
        new_step_items,
        new_response,
        run_handoffs,
        hooks,
        context_wrapper,
        run_config,
    ):
        """Patched execute_handoffs that ends agent span for handoffs"""

        # Call original method with all parameters
        result = await original_execute_handoffs(
            agent=agent,
            original_input=original_input,
            pre_step_items=pre_step_items,
            new_step_items=new_step_items,
            new_response=new_response,
            run_handoffs=run_handoffs,
            hooks=hooks,
            context_wrapper=context_wrapper,
            run_config=run_config,
        )

        # End span for current agent after handoff processing is complete
        if agent and context_wrapper and _has_active_agent_span(context_wrapper):
            _end_invoke_agent_span(context_wrapper, agent, None)

        return result

    @wraps(original_execute_final_output)
    async def patched_execute_final_output(
        cls,
        *,
        agent,
        original_input,
        new_response,
        pre_step_items,
        new_step_items,
        final_output,
        hooks,
        context_wrapper,
    ):
        """Patched execute_final_output that ends agent span for final outputs"""

        # Call original method with all parameters
        result = await original_execute_final_output(
            agent=agent,
            original_input=original_input,
            new_response=new_response,
            pre_step_items=pre_step_items,
            new_step_items=new_step_items,
            final_output=final_output,
            hooks=hooks,
            context_wrapper=context_wrapper,
        )

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

from sentry_sdk.integrations import DidNotEnable, Integration

from .patches import (
    _create_get_model_wrapper,
    _create_get_all_tools_wrapper,
    _create_run_wrapper,
    _create_run_streamed_wrapper,
    _patch_agent_run,
    _patch_error_tracing,
)

try:
    # "agents" is too generic. If someone has an agents.py file in their project
    # or another package that's importable via "agents", no ImportError would
    # be thrown and the integration would enable itself even if openai-agents is
    # not installed. That's why we're adding the second, more specific import
    # after it, even if we don't use it.
    import agents
    from agents.run import DEFAULT_AGENT_RUNNER

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _patch_runner() -> None:
    # Create the root span for one full agent run (including eventual handoffs)
    # Note agents.run.DEFAULT_AGENT_RUNNER.run_sync is a wrapper around
    # agents.run.DEFAULT_AGENT_RUNNER.run. It does not need to be wrapped separately.
    agents.run.DEFAULT_AGENT_RUNNER.run = _create_run_wrapper(
        agents.run.DEFAULT_AGENT_RUNNER.run
    )

    # Patch streaming runner
    agents.run.DEFAULT_AGENT_RUNNER.run_streamed = _create_run_streamed_wrapper(
        agents.run.DEFAULT_AGENT_RUNNER.run_streamed
    )

    # Creating the actual spans for each agent run (works for both streaming and non-streaming).
    _patch_agent_run()


def _patch_model() -> None:
    agents.run.AgentRunner._get_model = classmethod(
        _create_get_model_wrapper(agents.run.AgentRunner._get_model),
    )


def _patch_tools() -> None:
    agents.run.AgentRunner._get_all_tools = classmethod(
        _create_get_all_tools_wrapper(agents.run.AgentRunner._get_all_tools),
    )


class OpenAIAgentsIntegration(Integration):
    """
    NOTE: With version 0.8.0, the class methods below have been refactored to functions.
    - `AgentRunner._get_model()` -> `agents.run_internal.turn_preparation.get_model()`
    - `AgentRunner._get_all_tools()` -> `agents.run_internal.turn_preparation.get_all_tools()`
    - `AgentRunner._run_single_turn()` -> `agents.run_internal.run_loop.run_single_turn()`
    - `RunImpl.execute_handoffs()` -> `agents.run_internal.turn_resolution.execute_handoffs()`
    - `RunImpl.execute_final_output()` -> `agents.run_internal.turn_resolution.execute_final_output()`

    Typical interaction with the library:
    1. The user creates an Agent instance with configuration, including system instructions sent to every Responses API call.
    2. The user passes the agent instance to a Runner with `run()` and `run_streamed()` methods. The latter can be used to incrementally receive progress.
        - `Runner.run()` and `Runner.run_streamed()` are thin wrappers for `DEFAULT_AGENT_RUNNER.run()` and `DEFAULT_AGENT_RUNNER.run_streamed()`.
        - `DEFAULT_AGENT_RUNNER.run()` and `DEFAULT_AGENT_RUNNER.run_streamed()` are patched in `_patch_runner()` with `_create_run_wrapper()` and `_create_run_streamed_wrapper()`, respectively.
    3. In a loop, the agent repeatedly calls the Responses API, maintaining a conversation history that includes previous messages and tool results, which is passed to each call.
        - A Model instance is created at the start of the loop by calling the `Runner._get_model()`. We patch the Model instance using `_create_get_model_wrapper()` in `_patch_model()`.
        - Available tools are also deteremined at the start of the loop, with `Runner._get_all_tools()`. We patch Tool instances by iterating through the returned tools, in `_create_get_all_tools_wrapper()` called via `_patch_tools()`
        - In each loop iteration, `run_single_turn()` or `run_single_turn_streamed()` is responsible for calling the Responses API, patched with `patched_run_single_turn()` and `patched_run_single_turn_streamed()`.
    4. On loop termination, `RunImpl.execute_final_output()` is called. The function is patched with `patched_execute_final_output()`.

    Local tools are run based on the return value from the Responses API as a post-API call step in the above loop.
    Hosted MCP Tools are run as part of the Responses API call, and involve OpenAI reaching out to an external MCP server.
    An agent can handoff to another agent, also directed by the return value of the Responses API and run post-API call in the loop.
    Handoffs are a way to switch agent-wide configuration.
    - Handoffs are executed by calling `RunImpl.execute_handoffs()`. The method is patched in `patched_execute_handoffs()`
    """

    identifier = "openai_agents"

    @staticmethod
    def setup_once() -> None:
        _patch_error_tracing()
        _patch_tools()
        _patch_model()
        _patch_runner()

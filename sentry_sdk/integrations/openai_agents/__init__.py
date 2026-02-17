from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import parse_version

from functools import wraps

from .patches import (
    _get_model,
    _get_all_tools,
    _run_single_turn,
    _run_single_turn_streamed,
    _execute_handoffs,
    _create_run_wrapper,
    _create_run_streamed_wrapper,
    _execute_final_output,
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
    from agents.run import AgentRunner
    from agents.version import __version__ as OPENAI_AGENTS_VERSION

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


try:
    # AgentRunner methods moved in v0.8
    # https://github.com/openai/openai-agents-python/commit/3ce7c24d349b77bb750062b7e0e856d9ff48a5d5#diff-7470b3a5c5cbe2fcbb2703dc24f326f45a5819d853be2b1f395d122d278cd911
    from agents.run_internal import run_loop, turn_preparation, turn_resolution
except ImportError:
    run_loop = None
    turn_preparation = None
    turn_resolution = None

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    from agents.run_internal.run_steps import SingleStepResult


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
        - A Model instance is created at the start of the loop by calling the `Runner._get_model()`. We patch the Model instance using `patches._get_model()`.
        - Available tools are also deteremined at the start of the loop, with `Runner._get_all_tools()`. We patch Tool instances by iterating through the returned tools in `patches._get_all_tools()`.
        - In each loop iteration, `run_single_turn()` or `run_single_turn_streamed()` is responsible for calling the Responses API, patched with `patches._run_single_turn()` and `patches._run_single_turn_streamed()`.
    4. On loop termination, `RunImpl.execute_final_output()` is called. The function is patched with `patches._execute_final_output()`.

    Local tools are run based on the return value from the Responses API as a post-API call step in the above loop.
    Hosted MCP Tools are run as part of the Responses API call, and involve OpenAI reaching out to an external MCP server.
    An agent can handoff to another agent, also directed by the return value of the Responses API and run post-API call in the loop.
    Handoffs are a way to switch agent-wide configuration.
    - Handoffs are executed by calling `RunImpl.execute_handoffs()`. The method is patched with `patches._execute_handoffs()`
    """

    identifier = "openai_agents"

    @staticmethod
    def setup_once() -> None:
        _patch_error_tracing()
        _patch_runner()

        library_version = parse_version(OPENAI_AGENTS_VERSION)
        if library_version is not None and library_version >= (
            0,
            8,
        ):

            @wraps(run_loop.get_all_tools)
            async def new_wrapped_get_all_tools(
                agent: "agents.Agent",
                context_wrapper: "agents.RunContextWrapper",
            ) -> "list[agents.Tool]":
                return await _get_all_tools(
                    run_loop.get_all_tools, agent, context_wrapper
                )

            agents.run.get_all_tools = new_wrapped_get_all_tools

            @wraps(turn_preparation.get_model)
            def new_wrapped_get_model(
                agent: "agents.Agent", run_config: "agents.RunConfig"
            ) -> "agents.Model":
                return _get_model(turn_preparation.get_model, agent, run_config)

            agents.run_internal.run_loop.get_model = new_wrapped_get_model

            @wraps(run_loop.run_single_turn)
            async def new_wrapped_run_single_turn(
                *args: "Any", **kwargs: "Any"
            ) -> "SingleStepResult":
                return await _run_single_turn(run_loop.run_single_turn, *args, **kwargs)

            agents.run.run_single_turn = new_wrapped_run_single_turn

            @wraps(run_loop.run_single_turn_streamed)
            async def new_wrapped_run_single_turn_streamed(
                *args: "Any", **kwargs: "Any"
            ) -> "SingleStepResult":
                return await _run_single_turn_streamed(
                    run_loop.run_single_turn_streamed, *args, **kwargs
                )

            agents.run.run_single_turn_streamed = new_wrapped_run_single_turn_streamed

            original_execute_handoffs = turn_resolution.execute_handoffs

            @wraps(original_execute_handoffs)
            async def new_wrapped_execute_handoffs(
                *args: "Any", **kwargs: "Any"
            ) -> "SingleStepResult":
                return await _execute_handoffs(
                    original_execute_handoffs, *args, **kwargs
                )

            agents.run_internal.turn_resolution.execute_handoffs = (
                new_wrapped_execute_handoffs
            )

            original_execute_final_output = turn_resolution.execute_final_output

            @wraps(turn_resolution.execute_final_output)
            async def new_wrapped_final_output(
                *args: "Any", **kwargs: "Any"
            ) -> "SingleStepResult":
                return await _execute_final_output(
                    original_execute_final_output, *args, **kwargs
                )

            agents.run_internal.turn_resolution.execute_final_output = (
                new_wrapped_final_output
            )

            return

        original_get_all_tools = AgentRunner._get_all_tools

        @wraps(AgentRunner._get_all_tools.__func__)
        async def old_wrapped_get_all_tools(
            cls: "agents.Runner",
            agent: "agents.Agent",
            context_wrapper: "agents.RunContextWrapper",
        ) -> "list[agents.Tool]":
            return await _get_all_tools(original_get_all_tools, agent, context_wrapper)

        agents.run.AgentRunner._get_all_tools = classmethod(old_wrapped_get_all_tools)

        original_get_model = AgentRunner._get_model

        @wraps(AgentRunner._get_model.__func__)
        def old_wrapped_get_model(
            cls: "agents.Runner", agent: "agents.Agent", run_config: "agents.RunConfig"
        ) -> "agents.Model":
            return _get_model(original_get_model, agent, run_config)

        agents.run.AgentRunner._get_model = classmethod(old_wrapped_get_model)

        original_run_single_turn = AgentRunner._run_single_turn

        @wraps(AgentRunner._run_single_turn.__func__)
        async def old_wrapped_run_single_turn(
            cls: "agents.Runner", *args: "Any", **kwargs: "Any"
        ) -> "SingleStepResult":
            return await _run_single_turn(original_run_single_turn, *args, **kwargs)

        agents.run.AgentRunner._run_single_turn = classmethod(
            old_wrapped_run_single_turn
        )

        original_run_single_turn_streamed = AgentRunner._run_single_turn_streamed

        @wraps(AgentRunner._run_single_turn_streamed.__func__)
        async def old_wrapped_run_single_turn_streamed(
            cls: "agents.Runner", *args: "Any", **kwargs: "Any"
        ) -> "SingleStepResult":
            return await _run_single_turn_streamed(
                original_run_single_turn_streamed, *args, **kwargs
            )

        agents.run.AgentRunner._run_single_turn_streamed = classmethod(
            old_wrapped_run_single_turn_streamed
        )

        original_execute_handoffs = agents._run_impl.RunImpl.execute_handoffs

        @wraps(agents._run_impl.RunImpl.execute_handoffs.__func__)
        async def old_wrapped_execute_handoffs(
            cls: "agents.Runner", *args: "Any", **kwargs: "Any"
        ) -> "SingleStepResult":
            return await _execute_handoffs(original_execute_handoffs, *args, **kwargs)

        agents._run_impl.RunImpl.execute_handoffs = classmethod(
            old_wrapped_execute_handoffs
        )

        original_execute_final_output = agents._run_impl.RunImpl.execute_final_output

        @wraps(agents._run_impl.RunImpl.execute_final_output.__func__)
        async def old_wrapped_final_output(
            cls: "agents.Runner", *args: "Any", **kwargs: "Any"
        ) -> "SingleStepResult":
            return await _execute_final_output(
                original_execute_final_output, *args, **kwargs
            )

        agents._run_impl.RunImpl.execute_final_output = classmethod(
            old_wrapped_final_output
        )

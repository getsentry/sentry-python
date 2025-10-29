from sentry_sdk.integrations import DidNotEnable, Integration

from .patches import (
    _create_get_model_wrapper,
    _create_get_all_tools_wrapper,
    _create_run_wrapper,
    _patch_agent_run,
    _patch_error_tracing,
)

try:
    import agents

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _patch_runner():
    # type: () -> None
    # Create the root span for one full agent run (including eventual handoffs)
    # Note agents.run.DEFAULT_AGENT_RUNNER.run_sync is a wrapper around
    # agents.run.DEFAULT_AGENT_RUNNER.run. It does not need to be wrapped separately.
    # TODO-anton: Also patch streaming runner: agents.Runner.run_streamed
    agents.run.DEFAULT_AGENT_RUNNER.run = _create_run_wrapper(
        agents.run.DEFAULT_AGENT_RUNNER.run
    )

    # Creating the actual spans for each agent run.
    _patch_agent_run()


def _patch_model():
    # type: () -> None
    agents.run.AgentRunner._get_model = classmethod(
        _create_get_model_wrapper(agents.run.AgentRunner._get_model),
    )


def _patch_tools():
    # type: () -> None
    agents.run.AgentRunner._get_all_tools = classmethod(
        _create_get_all_tools_wrapper(agents.run.AgentRunner._get_all_tools),
    )


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_error_tracing()
        _patch_tools()
        _patch_model()
        _patch_runner()

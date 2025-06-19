from sentry_sdk.integrations import DidNotEnable, Integration

from .patches import (
    _create_get_model_wrapper,
    _create_get_all_tools_wrapper,
    _create_run_wrapper,
)

try:
    import agents

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _patch_runner():
    # type: () -> None

    # Creating agent workflow spans
    # Note agents.run.DEFAULT_AGENT_RUNNER.run_sync is a wrapper around
    # agents.run.DEFAULT_AGENT_RUNNER.run. It does not need to be wrapped separately.
    agents.run.DEFAULT_AGENT_RUNNER.run = _create_run_wrapper(
        agents.run.DEFAULT_AGENT_RUNNER.run
    )

    # TODO-anton: Also patch streaming runner: agents.Runner.run_streamed


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
        _patch_tools()
        _patch_model()
        _patch_runner()

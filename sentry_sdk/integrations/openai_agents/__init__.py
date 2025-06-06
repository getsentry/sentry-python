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
    agents.Runner.run = _create_run_wrapper(agents.Runner.run)
    agents.Runner.run_sync = _create_run_wrapper(agents.Runner.run_sync)
    agents.Runner.run_streamed = _create_run_wrapper(agents.Runner.run_streamed)


def _patch_model():
    # type: () -> None
    agents.Runner._get_model = _create_get_model_wrapper(agents.Runner._get_model)


def _patch_tools():
    # type: () -> None
    agents.Runner._get_all_tools = _create_get_all_tools_wrapper(
        agents.Runner._get_all_tools
    )


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"
    origin = f"auto.ai.{identifier}"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_tools()
        _patch_model()
        _patch_runner()

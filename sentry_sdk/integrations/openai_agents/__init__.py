from sentry_sdk.integrations import DidNotEnable, Integration

from .patches import (
    _create_get_model_wrapper,
    _create_get_all_tools_wrapper,
    _create_run_wrapper,
    _create_run_single_turn_wrapper,
)

try:
    import agents

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _patch_runner():
    # type: () -> None
    # Agent workflow spans
    agents.Runner.run = classmethod(
        _create_run_wrapper(agents.Runner.run),
    )
    agents.Runner.run_sync = classmethod(
        _create_run_wrapper(agents.Runner.run_sync),
    )
    agents.Runner.run_streamed = classmethod(
        _create_run_wrapper(agents.Runner.run_streamed),
    )

    # Agent invocation spans
    agents.Runner._run_single_turn = classmethod(
        _create_run_single_turn_wrapper(agents.Runner._run_single_turn),
    )


def _patch_model():
    # type: () -> None
    agents.Runner._get_model = classmethod(
        _create_get_model_wrapper(agents.Runner._get_model),
    )


def _patch_tools():
    # type: () -> None
    agents.Runner._get_all_tools = classmethod(
        _create_get_all_tools_wrapper(agents.Runner._get_all_tools),
    )


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_tools()
        _patch_model()
        _patch_runner()

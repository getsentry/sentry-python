from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.openai_agents.utils import _create_run_wrapper

try:
    import agents

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _patch_runner():
    # type: () -> None
    agents.Runner.run = _create_run_wrapper(agents.Runner.run)
    agents.Runner.run_sync = _create_run_wrapper(agents.Runner.run_sync)
    agents.Runner.run_streamed = _create_run_wrapper(agents.Runner.run_streamed)


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"
    origin = f"auto.ai.{identifier}"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_runner()

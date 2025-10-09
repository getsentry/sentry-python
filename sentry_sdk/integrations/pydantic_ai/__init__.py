from sentry_sdk.integrations import DidNotEnable, Integration

from .patches import (
    _patch_agent_run,
    _patch_graph_nodes,
    _patch_model_request,
    _patch_tool_execution,
)

try:
    import pydantic_ai

except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


class PydanticAIIntegration(Integration):
    identifier = "pydantic_ai"

    @staticmethod
    def setup_once():
        # type: () -> None
        """
        Set up the pydantic-ai integration.

        This patches the key methods in pydantic-ai to create Sentry spans for:
        - Agent workflow execution (root span)
        - Individual agent invocations
        - Model requests (AI client calls)
        - Tool executions
        """
        _patch_agent_run()
        _patch_graph_nodes()
        _patch_model_request()
        _patch_tool_execution()

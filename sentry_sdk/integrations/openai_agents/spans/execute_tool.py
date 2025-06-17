import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.scope import should_send_default_pii

from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from sentry_sdk import Span
    from typing import Any


def execute_tool_span(tool, *args, **kwargs):
    # type: (agents.Tool, *Any, **Any) -> Span
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_EXECUTE_TOOL,
        name=f"execute_tool {tool.name}",
        origin=sentry_sdk.integrations.openai_agents.OpenAIAgentsIntegration.origin,
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")

    if tool.__class__.__name__ == "FunctionTool":
        span.set_data(SPANDATA.GEN_AI_TOOL_TYPE, "function")

    span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool.name)
    span.set_data(SPANDATA.GEN_AI_TOOL_DESCRIPTION, tool.description)

    if should_send_default_pii():
        input = args[1]
        span.set_data(SPANDATA.GEN_AI_TOOL_INPUT, input)

    return span


def update_execute_tool_span(span, agent, tool, result):
    # type: (Span, agents.Agent, agents.Tool, Any) -> None
    _set_agent_data(span, agent)

    if should_send_default_pii():
        span.set_data(SPANDATA.GEN_AI_TOOL_OUTPUT, result)

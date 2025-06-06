import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from sentry_sdk import Span
    from typing import Any


def execute_tool_span(tool, *args, **kwargs):
    # type: (agents.Tool, *Any, **Any) -> Span
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_EXECUTE_TOOL, name=f"execute_tool {tool.name}"
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
    span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool.name)
    span.set_data(SPANDATA.GEN_AI_TOOL_DESCRIPTION, tool.description)

    input = args[1]
    span.set_data("gen_ai.tool.input", input)

    if tool.__class__.__name__ == "FunctionTool":
        span.set_data(SPANDATA.GEN_AI_TOOL_TYPE, "function")

    return span


def update_execute_tool_span(span, agent, tool, result):
    # type: (Span, agents.Agent, agents.Tool, Any) -> None
    _set_agent_data(span, agent)
    span.set_data("gen_ai.tool.output", result)

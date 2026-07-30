from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.utils import safe_serialize

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data, _should_send_prompts

if TYPE_CHECKING:
    from typing import Any, Optional

    from pydantic_ai._tool_manager import ToolDefinition  # type: ignore


def execute_tool_span(
    tool_name: str,
    tool_args: "Any",
    agent: "Any",
    tool_definition: "Optional[ToolDefinition]" = None,
) -> "StreamedSpan":
    """Create a span for tool execution.

    Args:
        tool_name: The name of the tool being executed
        tool_args: The arguments passed to the tool
        agent: The agent executing the tool
        tool_definition: The definition of the tool, if available
    """
    span = sentry_sdk.traces.start_span(
        name=f"execute_tool {tool_name}",
        attributes={
            "sentry.op": OP.GEN_AI_EXECUTE_TOOL,
            "sentry.origin": SPAN_ORIGIN,
            SPANDATA.GEN_AI_OPERATION_NAME: "execute_tool",
            SPANDATA.GEN_AI_TOOL_NAME: tool_name,
        },
    )

    if tool_definition is not None and hasattr(tool_definition, "description"):
        span.set_attribute(
            SPANDATA.GEN_AI_TOOL_DESCRIPTION,
            tool_definition.description,
        )

    _set_agent_data(span, agent)

    if _should_send_prompts() and tool_args is not None:
        span.set_attribute(SPANDATA.GEN_AI_TOOL_INPUT, safe_serialize(tool_args))

    return span


def update_execute_tool_span(span: "StreamedSpan", result: "Any") -> None:
    """Update the execute tool span with the result."""
    if not span:
        return

    if not _should_send_prompts() or result is None:
        return

    span.set_attribute(SPANDATA.GEN_AI_TOOL_OUTPUT, safe_serialize(result))

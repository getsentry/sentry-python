import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.utils import safe_serialize

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data, _should_send_prompts

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional


def execute_tool_span(
    tool_name, tool_args, agent, tool_type="function", parent_span=None
):
    # type: (str, Any, Any, str, Optional[sentry_sdk.tracing.Span]) -> sentry_sdk.tracing.Span
    """Create a span for tool execution.

    Args:
        tool_name: The name of the tool being executed
        tool_args: The arguments passed to the tool
        agent: The agent executing the tool
        tool_type: The type of tool ("function" for regular tools, "mcp" for MCP services)
        parent_span: Optional parent span to create this as a child of. If provided,
            uses parent_span.start_child() to ensure parallel tools are siblings.
    """
    if parent_span:
        # Create as child of the specified parent span
        # This ensures parallel tool calls are siblings under the same parent
        span = parent_span.start_child(
            op=OP.GEN_AI_EXECUTE_TOOL,
            name=f"execute_tool {tool_name}",
            origin=SPAN_ORIGIN,
        )
    else:
        # Create as child of current span
        span = sentry_sdk.start_span(
            op=OP.GEN_AI_EXECUTE_TOOL,
            name=f"execute_tool {tool_name}",
            origin=SPAN_ORIGIN,
        )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
    span.set_data(SPANDATA.GEN_AI_TOOL_TYPE, tool_type)
    span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool_name)

    _set_agent_data(span, agent)

    if _should_send_prompts() and tool_args is not None:
        span.set_data(SPANDATA.GEN_AI_TOOL_INPUT, safe_serialize(tool_args))

    return span


def update_execute_tool_span(span, result):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Update the execute tool span with the result."""
    if not span:
        return

    if _should_send_prompts() and result is not None:
        span.set_data(SPANDATA.GEN_AI_TOOL_OUTPUT, safe_serialize(result))

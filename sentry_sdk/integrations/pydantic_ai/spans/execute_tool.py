import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import safe_serialize

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def execute_tool_span(tool_name, tool_args, agent):
    # type: (str, Any, Any) -> sentry_sdk.tracing.Span
    """Create a span for tool execution."""
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_EXECUTE_TOOL,
        name=f"execute_tool {tool_name}",
        origin=SPAN_ORIGIN,
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
    span.set_data(SPANDATA.GEN_AI_TOOL_TYPE, "function")
    span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool_name)

    _set_agent_data(span, agent)

    if should_send_default_pii() and tool_args is not None:
        span.set_data(SPANDATA.GEN_AI_TOOL_INPUT, safe_serialize(tool_args))

    return span


def update_execute_tool_span(span, result):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Update the execute tool span with the result."""
    if not span:
        return

    if should_send_default_pii() and result is not None:
        span.set_data(SPANDATA.GEN_AI_TOOL_OUTPUT, safe_serialize(result))

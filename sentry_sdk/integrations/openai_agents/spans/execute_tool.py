from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.scope import should_send_default_pii

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data

if TYPE_CHECKING:
    from typing import Any

    import agents


def execute_tool_span(
    tool: "agents.Tool", *args: "Any", **kwargs: "Any"
) -> "sentry_sdk.tracing.Span":
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_EXECUTE_TOOL,
        name=f"execute_tool {tool.name}",
        origin=SPAN_ORIGIN,
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")

    span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool.name)
    span.set_data(SPANDATA.GEN_AI_TOOL_DESCRIPTION, tool.description)

    if should_send_default_pii():
        input = args[1]
        span.set_data(SPANDATA.GEN_AI_TOOL_INPUT, input)

    return span


def update_execute_tool_span(
    span: "sentry_sdk.tracing.Span",
    agent: "agents.Agent",
    tool: "agents.Tool",
    result: "Any",
) -> None:
    _set_agent_data(span, agent)

    if isinstance(result, str) and result.startswith(
        "An error occurred while running the tool"
    ):
        span.set_status(SPANSTATUS.INTERNAL_ERROR)

    if should_send_default_pii():
        span.set_data(SPANDATA.GEN_AI_TOOL_OUTPUT, result)

    # Add conversation ID from agent
    conv_id = getattr(agent, "_sentry_conversation_id", None)
    if conv_id:
        span.set_data(SPANDATA.GEN_AI_CONVERSATION_ID, conv_id)

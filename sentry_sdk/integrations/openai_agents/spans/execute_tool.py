from typing import TYPE_CHECKING

from sentry_conventions.attributes import ATTRIBUTE_NAMES

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.traces import Span, SpanStatus

from ..consts import SPAN_ORIGIN

if TYPE_CHECKING:
    from typing import Any

    import agents


def execute_tool_span(
    tool: "agents.FunctionTool", *args: "Any", **kwargs: "Any"
) -> "Span":
    return sentry_sdk.start_span(
        name=f"execute_tool {tool.name}",
        attributes={
            ATTRIBUTE_NAMES.SENTRY_OP: OP.GEN_AI_EXECUTE_TOOL,
            ATTRIBUTE_NAMES.SENTRY_ORIGIN: SPAN_ORIGIN,
            ATTRIBUTE_NAMES.GEN_AI_OPERATION_NAME: "execute_tool",
            ATTRIBUTE_NAMES.GEN_AI_TOOL_NAME: tool.name,
            ATTRIBUTE_NAMES.GEN_AI_TOOL_DESCRIPTION: tool.description,
        },
    )


def update_execute_tool_span(
    span: "Span",
    agent: "agents.Agent",
    tool: "agents.Tool",
    result: "Any",
) -> None:
    client = sentry_sdk.get_client()

    span.set_attribute(ATTRIBUTE_NAMES.GEN_AI_AGENT_NAME, agent.name)

    if isinstance(result, str) and result.startswith(
        "An error occurred while running the tool"
    ):
        span.status = SpanStatus.ERROR

    if client.options["data_collection"]["gen_ai"]["outputs"]:
        span.set_attribute(ATTRIBUTE_NAMES.GEN_AI_TOOL_CALL_RESULT, result)

    # Add conversation ID from agent
    conv_id = getattr(agent, "_sentry_conversation_id", None)
    if conv_id:
        span.set_attribute(ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID, conv_id)

from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import SpanStatus, StreamedSpan
from sentry_sdk.utils import has_data_collection_enabled

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data

if TYPE_CHECKING:
    from typing import Any, Union

    import agents


def execute_tool_span(
    tool: "agents.Tool", *args: "Any", **kwargs: "Any"
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    span = sentry_sdk.traces.start_span(
        name=f"execute_tool {tool.name}",
        attributes={
            "sentry.op": OP.GEN_AI_EXECUTE_TOOL,
            "sentry.origin": SPAN_ORIGIN,
            SPANDATA.GEN_AI_OPERATION_NAME: "execute_tool",
            SPANDATA.GEN_AI_TOOL_NAME: tool.name,
            SPANDATA.GEN_AI_TOOL_DESCRIPTION: tool.description,
        },
    )

    return span


def update_execute_tool_span(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    agent: "agents.Agent",
    tool: "agents.Tool",
    result: "Any",
) -> None:
    client = sentry_sdk.get_client()

    _set_agent_data(span, agent)

    if isinstance(result, str) and result.startswith(
        "An error occurred while running the tool"
    ):
        if isinstance(span, StreamedSpan):
            span.status = SpanStatus.ERROR
        else:
            span.set_status(SPANSTATUS.INTERNAL_ERROR)

    set_on_span = (
        span.set_attribute if isinstance(span, StreamedSpan) else span.set_data
    )

    if has_data_collection_enabled(client.options):
        if client.options["data_collection"]["gen_ai"]["outputs"]:
            set_on_span(SPANDATA.GEN_AI_TOOL_OUTPUT, result)
    elif should_send_default_pii():
        set_on_span(SPANDATA.GEN_AI_TOOL_OUTPUT, result)

    # Add conversation ID from agent
    conv_id = getattr(agent, "_sentry_conversation_id", None)
    if conv_id:
        set_on_span(SPANDATA.GEN_AI_CONVERSATION_ID, conv_id)

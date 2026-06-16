from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.tracing_utils import has_span_streaming_enabled

from ..consts import SPAN_ORIGIN

if TYPE_CHECKING:
    import agents


def handoff_span(
    context: "agents.RunContextWrapper", from_agent: "agents.Agent", to_agent_name: str
) -> None:
    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        with sentry_sdk.traces.start_span(
            name=f"handoff from {from_agent.name} to {to_agent_name}",
            attributes={
                "sentry.op": OP.GEN_AI_HANDOFF,
                "sentry.origin": SPAN_ORIGIN,
                SPANDATA.GEN_AI_OPERATION_NAME: "handoff",
            },
        ) as span:
            # Add conversation ID from agent
            conv_id = getattr(from_agent, "_sentry_conversation_id", None)
            if conv_id:
                span.set_attribute(SPANDATA.GEN_AI_CONVERSATION_ID, conv_id)
    else:
        with sentry_sdk.start_span(
            op=OP.GEN_AI_HANDOFF,
            name=f"handoff from {from_agent.name} to {to_agent_name}",
            origin=SPAN_ORIGIN,
        ) as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "handoff")

            # Add conversation ID from agent
            conv_id = getattr(from_agent, "_sentry_conversation_id", None)
            if conv_id:
                span.set_data(SPANDATA.GEN_AI_CONVERSATION_ID, conv_id)

from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN

if TYPE_CHECKING:
    import agents


def handoff_span(
    context: "agents.RunContextWrapper", from_agent: "agents.Agent", to_agent_name: str
) -> None:
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

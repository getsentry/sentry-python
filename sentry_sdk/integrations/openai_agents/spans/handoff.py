import sentry_sdk
from sentry_sdk.consts import ATTRS, OP

from ..consts import SPAN_ORIGIN

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents


def handoff_span(context, from_agent, to_agent_name):
    # type: (agents.RunContextWrapper, agents.Agent, str) -> None
    with sentry_sdk.start_span(
        op=OP.GEN_AI_HANDOFF,
        name=f"handoff from {from_agent.name} to {to_agent_name}",
        origin=SPAN_ORIGIN,
    ) as span:
        span.set_data(ATTRS.GEN_AI_OPERATION_NAME, "handoff")

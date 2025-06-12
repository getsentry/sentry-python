import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents


def handoff_span(context, from_agent, to_agent):
    # type: (agents.RunContextWrapper, agents.Agent, agents.Agent) -> None
    current_span = sentry_sdk.get_current_span()
    if current_span:
        with current_span.start_child(
            op=OP.GEN_AI_HANDOFF,
            name=f"handoff from {from_agent.name} to {to_agent.name}",
        ) as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "handoff")

        current_span.__exit__(None, None, None)

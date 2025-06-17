import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from typing import Any


def invoke_agent_span(agent):
    # type: (agents.Agent) -> sentry_sdk.tracing.Span
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_INVOKE_AGENT,
        name=f"invoke_agent {agent.name}",
        origin=SPAN_ORIGIN,
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    _set_agent_data(span, agent)

    return span


def update_invoke_agent_span(span, agent, output):
    # type: (sentry_sdk.tracing.span, agents.Agent, Any) -> None
    pass

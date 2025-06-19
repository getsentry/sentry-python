import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from typing import Any


def invoke_agent_span(context, agent):
    # type: (agents.RunContextWrapper, agents.Agent) -> sentry_sdk.tracing.Span
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_INVOKE_AGENT,
        name=f"invoke_agent {agent.name}",
        origin=SPAN_ORIGIN,
    )
    span.__enter__()

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    _set_agent_data(span, agent)

    return span


def update_invoke_agent_span(context, agent, output):
    # type: (agents.RunContextWrapper, agents.Agent, Any) -> None
    current_span = sentry_sdk.get_current_span()
    if current_span:
        current_span.__exit__(None, None, None)

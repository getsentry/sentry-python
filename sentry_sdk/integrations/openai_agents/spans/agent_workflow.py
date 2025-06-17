import sentry_sdk

from ..consts import SPAN_ORIGIN
from ..utils import _get_start_span_function

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent
    from typing import Any


def agent_workflow_span(agent):
    # type: (Agent) -> sentry_sdk.tracing.Span

    # Create a transaction or a span if an transaction is already active
    span = _get_start_span_function()(
        name=f"{agent.name} workflow",
        origin=SPAN_ORIGIN,
    )

    return span


def update_agent_workflow_span(span, agent, result):
    # type: (sentry_sdk.tracing.Span, Agent, Any) -> None
    pass

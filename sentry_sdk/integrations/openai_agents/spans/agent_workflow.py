from ..utils import _get_start_span_function

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent
    from sentry_sdk import Span
    from typing import Any


def agent_workflow_span(*args, **kwargs):
    # type: (*Any, **Any) -> Span
    agent = args[0]
    # Create a transaction or a span if an transaction is already active
    span = _get_start_span_function()(
        name=f"{agent.name} workflow",
    )

    return span


def update_agent_workflow_span(span, agent, result):
    # type: (Span, Agent, Any) -> None
    pass

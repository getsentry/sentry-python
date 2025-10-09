import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function
from sentry_sdk.consts import OP

from ..consts import SPAN_ORIGIN

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def agent_workflow_span(agent):
    # type: (Any) -> sentry_sdk.tracing.Span
    """Create a root span for the entire agent workflow."""
    start_span_function = get_start_span_function()

    agent_name = "agent"
    if agent and hasattr(agent, "name") and agent.name:
        agent_name = agent.name

    span = start_span_function(
        op=OP.GEN_AI_PIPELINE,
        name=f"agent workflow {agent_name}",
        origin=SPAN_ORIGIN,
    )

    return span

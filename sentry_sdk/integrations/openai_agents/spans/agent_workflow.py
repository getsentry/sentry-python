from typing import TYPE_CHECKING

import sentry_sdk

from ..consts import SPAN_ORIGIN

if TYPE_CHECKING:
    import agents


def agent_workflow_span(
    agent: "agents.Agent",
) -> "sentry_sdk.traces.StreamedSpan":
    # Create a transaction or a span if an transaction is already active
    span = sentry_sdk.traces.start_span(
        name=f"{agent.name} workflow", attributes={"sentry.origin": SPAN_ORIGIN}
    )

    return span

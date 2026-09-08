from typing import TYPE_CHECKING

import sentry_sdk

from ..consts import SPAN_ORIGIN

if TYPE_CHECKING:
    from typing import Union

    import agents


def agent_workflow_span(
    agent: "agents.Agent",
) -> "Union[sentry_sdk.tracing.Span, sentry_sdk.traces.StreamedSpan]":
    return sentry_sdk.traces.start_span(
        name=f"{agent.name} workflow", attributes={"sentry.origin": SPAN_ORIGIN}
    )

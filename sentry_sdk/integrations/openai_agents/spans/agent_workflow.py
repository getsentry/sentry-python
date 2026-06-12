from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function
from sentry_sdk.tracing_utils import has_span_streaming_enabled

from ..consts import SPAN_ORIGIN

if TYPE_CHECKING:
    from typing import Union

    import agents


def agent_workflow_span(
    agent: "agents.Agent",
) -> "Union[sentry_sdk.tracing.Span, sentry_sdk.traces.StreamedSpan]":
    # Create a transaction or a span if an transaction is already active
    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        span = sentry_sdk.traces.start_span(
            name=f"{agent.name} workflow", attributes={"sentry.origin": SPAN_ORIGIN}
        )

        return span

    span = get_start_span_function()(
        name=f"{agent.name} workflow",
        origin=SPAN_ORIGIN,
    )

    return span

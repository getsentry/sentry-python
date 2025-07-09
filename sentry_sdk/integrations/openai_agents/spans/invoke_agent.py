from __future__ import annotations

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from typing import Any


def invoke_agent_span(
    context_wrapper: agents.RunContextWrapper, agent: agents.Agent
) -> sentry_sdk.tracing.Span:
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_INVOKE_AGENT,
        name=f"invoke_agent {agent.name}",
        origin=SPAN_ORIGIN,
    )
    span.__enter__()

    span.set_attribute(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    _set_agent_data(span, agent)

    context_wrapper._sentry_invoke_agent_span = span

    return span


def update_invoke_agent_span(
    context_wrapper: agents.RunContextWrapper, agent: agents.Agent, output: Any
) -> None:
    span = getattr(context_wrapper, "_sentry_invoke_agent_span", None)
    if span is not None:
        span.__exit__(None, None, None)
        del context_wrapper._sentry_invoke_agent_span

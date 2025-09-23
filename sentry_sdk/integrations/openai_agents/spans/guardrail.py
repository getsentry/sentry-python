import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from typing import Any


def guardrail_span(guardrail, guardrail_type, args, kwargs):
    # type: (agents.Guardrail, tuple[Any, ...], dict[str, Any]) -> sentry_sdk.tracing.Span
    op = (
        OP.GEN_AI_GUARDRAIL_OUTPUT
        if guardrail_type == "output"
        else OP.GEN_AI_GUARDRAIL_INPUT
    )

    span = sentry_sdk.start_span(
        op=op,
        name=f"guardrail {guardrail.name or ''}".strip(),
        origin=SPAN_ORIGIN,
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "guardrail")
    span.set_data(SPANDATA.GEN_AI_TOOL_TYPE, f"guardrail.{guardrail_type}")

    if guardrail.name is not None:
        span.set_data(SPANDATA.GEN_AI_TOOL_NAME, guardrail.name)

    try:
        input = args[2]
    except IndexError:
        input = None

    if input is not None:
        span.set_data(SPANDATA.GEN_AI_TOOL_INPUT, input)

    return span


def update_guardrail_span(span, agent, guardrail, guardrail_type, result):
    # type: (sentry_sdk.tracing.Span, agents.Agent, agents.Guardrail, Any) -> None
    if agent.name is not None:
        span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent.name)

    output = result.output.output_info.get("reason")
    if output is not None:
        span.set_data(SPANDATA.GEN_AI_TOOL_OUTPUT, output)

    tripwire_triggered = result.output.tripwire_triggered
    if tripwire_triggered is not None:
        span.set_data(SPANDATA.GEN_AI_GUARDRAIL_TRIPWIRE_TRIGGERED, tripwire_triggered)

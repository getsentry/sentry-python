import sentry_sdk
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.scope import should_send_default_pii

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from typing import Any


def invoke_agent_span(context, agent, kwargs):
    # type: (agents.RunContextWrapper, agents.Agent, dict[str, Any]) -> sentry_sdk.tracing.Span
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_INVOKE_AGENT,
        name=f"invoke_agent {agent.name}",
        origin=SPAN_ORIGIN,
    )
    span.__enter__()

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    if should_send_default_pii():
        messages = []
        if agent.instructions:
            messages.append(
                {
                    "content": [{"text": agent.instructions, "type": "text"}],
                    "role": "system",
                }
            )

        if "original_input" in kwargs and kwargs["original_input"] is not None:
            messages.append(
                {
                    "content": [{"text": kwargs["original_input"], "type": "text"}],
                    "role": "user",
                }
            )

        if len(messages) > 0:
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages, unpack=False
            )

    _set_agent_data(span, agent)

    return span


def update_invoke_agent_span(context, agent, output):
    # type: (agents.RunContextWrapper, agents.Agent, Any) -> None
    span = sentry_sdk.get_current_span()

    if span:
        if should_send_default_pii():
            set_data_normalized(
                span, SPANDATA.GEN_AI_RESPONSE_TEXT, output, unpack=False
            )

        span.__exit__(None, None, None)

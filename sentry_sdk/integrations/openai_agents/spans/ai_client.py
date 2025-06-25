import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_input_data,
    _set_output_data,
    _set_usage_data,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent
    from typing import Any


def ai_client_span(agent, get_response_kwargs):
    # type: (Agent, dict[str, Any]) -> sentry_sdk.tracing.Span
    # TODO-anton: implement other types of operations. Now "chat" is hardcoded.
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_CHAT,
        description=f"chat {agent.model}",
        origin=SPAN_ORIGIN,
    )
    # TODO-anton: remove hardcoded stuff and replace something that also works for embedding and so on
    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    return span


def update_ai_client_span(span, agent, get_response_kwargs, result):
    # type: (sentry_sdk.tracing.Span, Agent, dict[str, Any], Any) -> None
    _set_agent_data(span, agent)
    _set_usage_data(span, result.usage)
    _set_input_data(span, get_response_kwargs)
    _set_output_data(span, result)

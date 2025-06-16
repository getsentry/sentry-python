import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..utils import _set_agent_data, _set_input_data, _set_output_data, _set_usage_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent, Model, RunConfig
    from sentry_sdk import Span


def ai_client_span(agent, model, run_config, get_response_kwargs):
    # type: (Agent, Model, RunConfig, dict) -> Span
    # TODO-anton: implement other types of operations. Now "chat" is hardcoded.
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_CHAT,
        description=f"chat {agent.model}",
    )
    # TODO-anton: remove hardcoded stuff and replace something that also works for embedding and so on
    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    return span


def update_ai_client_span(span, agent, model, run_config, get_response_kwargs, result):
    _set_agent_data(span, agent)
    _set_usage_data(span, result.usage)
    _set_input_data(span, get_response_kwargs)
    _set_output_data(span, result)

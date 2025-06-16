import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..utils import _set_agent_data, _set_input_data, _set_usage_data

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

    # LLM Response OUTPUT
    output_messages = {
        "response": [],
        "tool": [],
    }
    for output in result.output:
        if output.type == "function_call":
            output_messages["tool"].append(output.dict())
        elif output.type == "message":
            for output_message in output.content:
                output_messages["response"].append(output_message.to_json())

    if len(output_messages["tool"]) > 0:
        span.set_data(SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, output_messages["tool"])

    if len(output_messages["response"]) > 0:
        span.set_data(SPANDATA.GEN_AI_CHOICE, output_messages["response"])

        # Deprecated name just for first iteration.
        # TODO-anton: define how to set tool response messages and document in sentry-conventions.
        span.set_data("gen_ai.response.text", output_messages["response"])

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..utils import _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent, Model, RunConfig
    from sentry_sdk import Span


def ai_client_span(agent, model, run_config, get_response_kwargs):
    # type: (Agent, Model, RunConfig, dict) -> Span
    # TODO-anton: implement other types of operations
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_CHAT,
        description=f"*chat* (TODO: remove hardcoded stuff) {agent.model}",
    )
    # TODO-anton: remove hardcoded stuff and replace something that also works for embedding and so on
    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    return span


def update_ai_client_span(span, agent, model, run_config, get_response_kwargs, result):
    _set_agent_data(span, agent)

    if get_response_kwargs.get("system_instructions"):
        span.set_data(
            SPANDATA.GEN_AI_SYSTEM_MESSAGE,
            get_response_kwargs.get("system_instructions"),
        )

    # Deprecated name just for first iteration.
    # TODO-anton: define how to set input message and document in sentry-conventions.
    span.set_data("ai.prompt.messages", get_response_kwargs.get("input", []))

    for message in get_response_kwargs.get("input", []):
        if message.get("role") == "user":
            span.set_data(SPANDATA.GEN_AI_USER_MESSAGE, message.get("content"))
            break

    span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, result.usage.input_tokens)
    span.set_data(
        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
        result.usage.input_tokens_details.cached_tokens,
    )
    span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, result.usage.output_tokens)
    span.set_data(
        SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
        result.usage.output_tokens_details.reasoning_tokens,
    )
    span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, result.usage.total_tokens)

    # Deprecated name just for first iteration.
    # TODO-anton: define how to set tool response messages and document in sentry-conventions.
    tool_calls = [
        item.to_json()
        for item in result.output
        if item.__class__.__name__ == "ResponseFunctionToolCall"
    ]
    span.set_data("ai.response.toolCalls", tool_calls)

    output = [item.to_json() for item in result.output]
    span.set_data(SPANDATA.GEN_AI_CHOICE, output)

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..utils import _set_agent_data

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

    system_instructions = get_response_kwargs.get("system_instructions")
    if system_instructions:
        span.set_data(SPANDATA.GEN_AI_SYSTEM_MESSAGE, system_instructions)

    # "system" messages have a content of type string.
    prompt_messages = []
    if system_instructions:
        prompt_messages.append({"role": "system", "content": system_instructions})

    # "user", "assistant", "tool" messages have a content of type array of objects.
    other_messages = {
        "user": [],
        "assistant": [],
        "tool": [],
    }
    for message in get_response_kwargs.get("input", []):
        if "role" in message:
            other_messages[message.get("role")].append(
                {"type": "text", "text": message.get("content")}
            )
        else:
            if message.get("type") == "function_call":
                other_messages["assistant"].append(message)
            elif message.get("type") == "function_call_output":
                other_messages["tool"].append(message)

    for role, messages in other_messages.items():
        if len(messages) > 0:
            prompt_messages.append({"role": role, "content": messages})

    # Deprecated name just for first iteration.
    # TODO-anton: define how to set input message and document in sentry-conventions.
    span.set_data("ai.prompt.format", "messages")
    span.set_data("ai.prompt.messages", prompt_messages)

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
    assistant_responses = [
        item.to_json()
        for item in result.output
        if item.__class__.__name__ == "ResponseFunctionToolCall"
    ]
    if len(assistant_responses) > 0:
        span.set_data("ai.response.toolCalls", assistant_responses)

    output = [item.to_json() for item in result.output]
    span.set_data(SPANDATA.GEN_AI_CHOICE, output)

    # Deprecated name just for first iteration.
    # TODO-anton: define how to set tool response messages and document in sentry-conventions.
    span.set_data("ai.response.text", output)

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

    # LLM Request INPUT

    prompt_messages = []

    system_instructions = get_response_kwargs.get("system_instructions")
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
    span.set_data("gen_ai.request.messages", prompt_messages)

    # LLM Request USAGE

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

    # LLM Response OUTPUT

    # Deprecated name just for first iteration.
    # TODO-anton: define how to set tool response messages and document in sentry-conventions.

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
        span.set_data("gen_ai.response.tool_calls", output_messages["tool"])

    if len(output_messages["response"]) > 0:
        span.set_data(SPANDATA.GEN_AI_CHOICE, output_messages["response"])

        # Deprecated name just for first iteration.
        # TODO-anton: define how to set tool response messages and document in sentry-conventions.
        span.set_data("gen_ai.response.text", output_messages["response"])

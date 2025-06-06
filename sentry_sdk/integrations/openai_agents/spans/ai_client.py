import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..utils import _set_agent_data


def ai_client_span(agent, model, run_config, get_response_kwargs):
    """
    https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/#inference

    gen_ai.operation.name # chat|generate_content|text_completion|embeddings
    gen_ai.system
    gen_ai.conversation.id
    gen_ai.output.type
    gen_ai.request.choice.count
    gen_ai.request.model
    gen_ai.request.seed
    gen_ai.request.frequency_penalty
    gen_ai.request.max_tokens
    gen_ai.request.presence_penalty
    gen_ai.request.stop_sequences
    gen_ai.request.temperature
    gen_ai.request.top_k
    gen_ai.request.top_p
    gen_ai.response.finish_reasons
    gen_ai.response.id
    gen_ai.response.model
    gen_ai.usage.input_tokens
    gen_ai.usage.output_tokens
    server.address
    server.port
    error.type


    INPUT:
    gen_ai.system.message
    gen_ai.user.message
    gen_ai.assistant.message
    gen_ai.tool.message (response from tool call as input)

    OUTPUT:
    gen_ai.choice
    """
    # TODO-anton: implement other types of operations
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_CHAT,
        description=f"*chat* (TODO: remove hardcoded stuff) {agent.model}",
    )
    # TODO-anton: remove hardcoded stuff and replace something that also works for embedding and so on
    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    return span


def finish_ai_client_span(agent, model, run_config, get_response_kwargs, result):
    _set_agent_data(agent)

    span = sentry_sdk.get_current_span()

    if get_response_kwargs.get("system_instructions"):
        span.set_data(
            SPANDATA.GEN_AI_SYSTEM_MESSAGE,
            get_response_kwargs.get("system_instructions"),
        )

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

    output = [item.to_json() for item in result.output]
    span.set_data(SPANDATA.GEN_AI_CHOICE, output)

    # TODO-anton: for debugging, remove this
    for index, message in enumerate(result.output):
        span.set_data(f"DEBUG.output.{index}", message)
        # if message.get("type") == "function_call":

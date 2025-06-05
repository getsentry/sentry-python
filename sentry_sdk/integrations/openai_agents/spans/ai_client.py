import sentry_sdk

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
    return sentry_sdk.start_span(
        op="gen_ai.chat",
        description=f"*chat* (TODO: remove hardcoded stuff) {agent.model}",
    )


def finish_ai_client_span(agent, model, run_config, get_response_kwargs, result):
    _set_agent_data(agent)

    span = sentry_sdk.get_current_span()

    if get_response_kwargs.get("system_instructions"):
        span.set_data(
            "gen_ai.system.message", get_response_kwargs.get("system_instructions")
        )

    for message in get_response_kwargs.get("input", []):
        if message.get("role") == "user":
            span.set_data("gen_ai.user.message", message.get("content"))
            break

    for index, message in enumerate(result.output):
        span.set_data(f"output.{index}", message)
        # if message.get("type") == "function_call":

    span.set_data("gen_ai.usage.input_tokens", result.usage.input_tokens)
    span.set_data(
        "gen_ai.usage.input_tokens.cached",
        result.usage.input_tokens_details.cached_tokens,
    )
    span.set_data("gen_ai.usage.output_tokens", result.usage.output_tokens)
    span.set_data(
        "gen_ai.usage.output_tokens.reasoning",
        result.usage.output_tokens_details.reasoning_tokens,
    )
    span.set_data("gen_ai.usage.total_tokens", result.usage.total_tokens)

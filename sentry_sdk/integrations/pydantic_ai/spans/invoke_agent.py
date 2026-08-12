from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import (
    _set_span_data_attribute,
    get_start_span_function,
    normalize_message_roles,
    set_data_normalized,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.tracing_utils import (
    has_span_streaming_enabled,
    should_truncate_gen_ai_input,
)

from .._extract import extract_agent_prompt_messages, extract_response_model_name
from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_available_tools,
    _set_model_data,
    _should_send_prompts,
)

if TYPE_CHECKING:
    from typing import Any, Union

    from sentry_sdk.traces import StreamedSpan


def invoke_agent_span(
    user_prompt: "Any",
    agent: "Any",
    model: "Any",
    model_settings: "Any",
    is_streaming: bool = False,
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    """Create a span for invoking the agent."""
    # Determine agent name for span
    name = "agent"
    if agent and getattr(agent, "name", None):
        name = agent.name

    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        span = sentry_sdk.traces.start_span(
            name=f"invoke_agent {name}",
            attributes={
                "sentry.op": OP.GEN_AI_INVOKE_AGENT,
                "sentry.origin": SPAN_ORIGIN,
                SPANDATA.GEN_AI_OPERATION_NAME: "invoke_agent",
            },
        )
    else:
        span = get_start_span_function()(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=f"invoke_agent {name}",
            origin=SPAN_ORIGIN,
        )

        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    _set_agent_data(span, agent)
    _set_model_data(span, model, model_settings)
    _set_available_tools(span, agent)

    # Add user prompt and system prompts if available and prompts are enabled
    if _should_send_prompts():
        messages = extract_agent_prompt_messages(agent, user_prompt)

        if messages:
            normalized_messages = normalize_message_roles(messages)
            client = sentry_sdk.get_client()
            scope = sentry_sdk.get_current_scope()
            messages_data = (
                truncate_and_annotate_messages(normalized_messages, span, scope)
                if should_truncate_gen_ai_input(client.options)
                else normalized_messages
            )
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages_data, unpack=False
            )

    return span


def update_invoke_agent_span(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    result: "Any",
) -> None:
    """Update and close the invoke agent span."""
    if not span or not result:
        return

    # Extract output from result
    output = getattr(result, "output", None)

    # Set response text if prompts are enabled
    if _should_send_prompts() and output:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, str(output), unpack=False
        )

    # Set model name from response if available
    response_model_name = extract_response_model_name(result)
    if response_model_name:
        _set_span_data_attribute(
            span, SPANDATA.GEN_AI_RESPONSE_MODEL, response_model_name
        )

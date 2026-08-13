import json
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import (
    _set_span_data_attribute,
    normalize_message_roles,
    set_data_normalized,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.tracing_utils import (
    has_span_streaming_enabled,
    should_truncate_gen_ai_input,
)

from .._extract import (
    extract_model_info,
    extract_request_messages,
    extract_response_parts,
    extract_system_instructions,
)
from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_available_tools,
    _set_model_data,
    _should_send_prompts,
    get_current_agent,
    get_is_streaming,
)
from .utils import _set_usage_data

if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from pydantic_ai.messages import ModelResponse

    from sentry_sdk.traces import StreamedSpan


def _set_input_messages(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]", messages: "Any"
) -> None:
    """Set input messages data on a span."""
    if not _should_send_prompts():
        return

    if not messages:
        return

    system_instructions = extract_system_instructions(messages)
    if system_instructions:
        _set_span_data_attribute(
            span,
            SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
            json.dumps(system_instructions),
        )

    try:
        formatted_messages = extract_request_messages(messages)

        if formatted_messages:
            normalized_messages = normalize_message_roles(formatted_messages)
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
    except Exception:
        # If we fail to format messages, just skip it
        pass


def _set_output_data(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    response: "Optional[ModelResponse]",
) -> None:
    """Set output data on a span."""
    if not _should_send_prompts():
        return

    if not response:
        return

    if response.model_name:
        _set_span_data_attribute(
            span, SPANDATA.GEN_AI_RESPONSE_MODEL, response.model_name
        )

    try:
        parts = extract_response_parts(response)
        if parts:
            _set_span_data_attribute(
                span,
                SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                json.dumps([{"role": "assistant", "parts": parts}]),
            )
    except Exception:
        # If we fail to format output, just skip it
        pass


def ai_client_span(
    messages: "Any", agent: "Any", model: "Any", model_settings: "Any"
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    """Create a span for an AI client call (model request).

    Args:
        messages: Full conversation history (list of messages)
        agent: Agent object
        model: Model object
        model_settings: Model settings
    """
    # Determine model name for span name, resolving the same way as
    # _set_model_data so the span name and gen_ai.request.model agree
    model_name = (
        extract_model_info(model, None, agent or get_current_agent()).name or "unknown"
    )

    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        span = sentry_sdk.traces.start_span(
            name=f"chat {model_name}",
            attributes={
                "sentry.op": OP.GEN_AI_CHAT,
                "sentry.origin": SPAN_ORIGIN,
                SPANDATA.GEN_AI_OPERATION_NAME: "chat",
                SPANDATA.GEN_AI_RESPONSE_STREAMING: get_is_streaming(),
            },
        )
    else:
        span = sentry_sdk.start_span(
            op=OP.GEN_AI_CHAT,
            name=f"chat {model_name}",
            origin=SPAN_ORIGIN,
        )

        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
        # Set streaming flag from contextvar
        span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, get_is_streaming())

    _set_agent_data(span, agent)
    _set_model_data(span, model, model_settings)

    # Add available tools if agent is available
    agent_obj = agent or get_current_agent()
    _set_available_tools(span, agent_obj)

    # Set input messages (full conversation history)
    if messages:
        _set_input_messages(span, messages)

    return span


def update_ai_client_span(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    model_response: "Optional[ModelResponse]",
) -> None:
    """Update the AI client span with response data."""
    if not span:
        return

    # Set usage data if available
    if model_response and hasattr(model_response, "usage"):
        _set_usage_data(span, model_response.usage)

    # Set output data
    _set_output_data(span, model_response)

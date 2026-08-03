from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import (
    get_start_span_function,
    normalize_message_roles,
    set_data_normalized,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import (
    has_span_streaming_enabled,
    should_truncate_gen_ai_input,
)

from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_available_tools,
    _set_model_data,
    _should_send_prompts,
)
from .utils import (
    _serialize_binary_content_item,
    _serialize_image_url_item,
)

if TYPE_CHECKING:
    from typing import Any, Union

try:
    from pydantic_ai.messages import BinaryContent, ImageUrl  # type: ignore
except ImportError:
    BinaryContent = None
    ImageUrl = None


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
        messages = []

        # Add system prompts (both instructions and system_prompt)
        system_texts = []

        if agent:
            # Check for system_prompt
            system_prompts = getattr(agent, "_system_prompts", None) or []
            for prompt in system_prompts:
                if isinstance(prompt, str):
                    system_texts.append(prompt)

            # Check for instructions (stored in _instructions)
            instructions = getattr(agent, "_instructions", None)
            if instructions:
                if isinstance(instructions, str):
                    system_texts.append(instructions)
                elif isinstance(instructions, (list, tuple)):
                    for instr in instructions:
                        if isinstance(instr, str):
                            system_texts.append(instr)
                        elif callable(instr):
                            # Skip dynamic/callable instructions
                            pass

        # Add all system texts as system messages
        for system_text in system_texts:
            messages.append(
                {
                    "content": [{"text": system_text, "type": "text"}],
                    "role": "system",
                }
            )

        # Add user prompt
        if user_prompt:
            if isinstance(user_prompt, str):
                messages.append(
                    {
                        "content": [{"text": user_prompt, "type": "text"}],
                        "role": "user",
                    }
                )
            elif isinstance(user_prompt, list):
                # Handle list of user content
                content = []
                for item in user_prompt:
                    if isinstance(item, str):
                        content.append({"text": item, "type": "text"})
                    elif ImageUrl and isinstance(item, ImageUrl):
                        content.append(_serialize_image_url_item(item))
                    elif BinaryContent and isinstance(item, BinaryContent):
                        content.append(_serialize_binary_content_item(item))
                if content:
                    messages.append(
                        {
                            "content": content,
                            "role": "user",
                        }
                    )

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
    if hasattr(result, "response"):
        try:
            response = result.response
            if hasattr(response, "model_name") and response.model_name:
                if isinstance(span, StreamedSpan):
                    span.set_attribute(
                        SPANDATA.GEN_AI_RESPONSE_MODEL, response.model_name
                    )
                else:
                    span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, response.model_name)
        except Exception:
            # If response access fails, continue without setting model name
            pass

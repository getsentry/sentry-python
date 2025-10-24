import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function, set_data_normalized
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_available_tools,
    _set_model_data,
    _should_send_prompts,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def invoke_agent_span(user_prompt, agent, model, model_settings):
    # type: (Any, Any, Any, Any) -> sentry_sdk.tracing.Span
    """Create a span for invoking the agent."""
    # Determine agent name for span
    name = "agent"
    if agent and getattr(agent, "name", None):
        name = agent.name

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
                if content:
                    messages.append(
                        {
                            "content": content,
                            "role": "user",
                        }
                    )

        if messages:
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages, unpack=False
            )

    return span


def update_invoke_agent_span(span, output):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Update and close the invoke agent span."""
    if span and _should_send_prompts() and output:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, str(output), unpack=False
        )

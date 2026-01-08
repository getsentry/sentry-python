import sentry_sdk
from sentry_sdk.ai.utils import (
    get_start_span_function,
    set_data_normalized,
    normalize_message_roles,
    normalize_message_role,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import safe_serialize

from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_usage_data,
    _transform_openai_agents_message_content,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from typing import Any, Optional


def invoke_agent_span(
    context: "agents.RunContextWrapper", agent: "agents.Agent", kwargs: "dict[str, Any]"
) -> "sentry_sdk.tracing.Span":
    start_span_function = get_start_span_function()
    span = start_span_function(
        op=OP.GEN_AI_INVOKE_AGENT,
        name=f"invoke_agent {agent.name}",
        origin=SPAN_ORIGIN,
    )
    span.__enter__()

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    if should_send_default_pii():
        messages = []
        if agent.instructions:
            message = (
                agent.instructions
                if isinstance(agent.instructions, str)
                else safe_serialize(agent.instructions)
            )
            messages.append(
                {
                    "content": [{"text": message, "type": "text"}],
                    "role": "system",
                }
            )

        original_input = kwargs.get("original_input")
        if original_input is not None:
            if isinstance(original_input, str):
                # String input: wrap in text block
                messages.append(
                    {
                        "content": [{"text": original_input, "type": "text"}],
                        "role": "user",
                    }
                )
            elif isinstance(original_input, list) and len(original_input) > 0:
                # Check if list contains message objects (with type="message")
                # or content parts (input_text, input_image, etc.)
                first_item = original_input[0]
                if isinstance(first_item, dict) and first_item.get("type") == "message":
                    # List of message objects - process each individually
                    for msg in original_input:
                        if isinstance(msg, dict) and msg.get("type") == "message":
                            role = normalize_message_role(msg.get("role", "user"))
                            content = msg.get("content")
                            transformed = _transform_openai_agents_message_content(
                                content
                            )
                            if isinstance(transformed, str):
                                transformed = [{"text": transformed, "type": "text"}]
                            elif not isinstance(transformed, list):
                                transformed = [
                                    {"text": str(transformed), "type": "text"}
                                ]
                            messages.append({"content": transformed, "role": role})
                else:
                    # List of content parts - transform and wrap as user message
                    content = _transform_openai_agents_message_content(original_input)
                    if not isinstance(content, list):
                        content = [{"text": str(content), "type": "text"}]
                    messages.append({"content": content, "role": "user"})

        if len(messages) > 0:
            normalized_messages = normalize_message_roles(messages)
            scope = sentry_sdk.get_current_scope()
            messages_data = truncate_and_annotate_messages(
                normalized_messages, span, scope
            )
            if messages_data is not None:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_MESSAGES,
                    messages_data,
                    unpack=False,
                )

    _set_agent_data(span, agent)

    return span


def update_invoke_agent_span(
    context: "agents.RunContextWrapper", agent: "agents.Agent", output: "Any"
) -> None:
    span = getattr(context, "_sentry_agent_span", None)

    if span:
        # Add aggregated usage data from context_wrapper
        if hasattr(context, "usage"):
            _set_usage_data(span, context.usage)

        if should_send_default_pii():
            set_data_normalized(
                span, SPANDATA.GEN_AI_RESPONSE_TEXT, output, unpack=False
            )

        span.__exit__(None, None, None)
        delattr(context, "_sentry_agent_span")


def end_invoke_agent_span(
    context_wrapper: "agents.RunContextWrapper",
    agent: "agents.Agent",
    output: "Optional[Any]" = None,
) -> None:
    """End the agent invocation span"""
    # Clear the stored agent
    if hasattr(context_wrapper, "_sentry_current_agent"):
        delattr(context_wrapper, "_sentry_current_agent")

    update_invoke_agent_span(context_wrapper, agent, output)

from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import (
    normalize_message_roles,
    set_data_normalized,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import (
    has_span_streaming_enabled,
)
from sentry_sdk.utils import has_data_collection_enabled, safe_serialize

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data, _set_usage_data

if TYPE_CHECKING:
    from typing import Any, Union

    import agents


def invoke_agent_span(
    context: "agents.RunContextWrapper", agent: "agents.Agent", kwargs: "dict[str, Any]"
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    client_options = sentry_sdk.get_client().options
    span = sentry_sdk.traces.start_span(
        name=f"invoke_agent {agent.name}",
        attributes={
            "sentry.op": OP.GEN_AI_INVOKE_AGENT,
            "sentry.origin": SPAN_ORIGIN,
            SPANDATA.GEN_AI_OPERATION_NAME: "invoke_agent",
        },
    )

    record_inputs = False
    if has_data_collection_enabled(client_options):
        if client_options["data_collection"]["gen_ai"]["inputs"]:
            record_inputs = True
    elif should_send_default_pii():
        record_inputs = True

    if record_inputs:
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
            message = (
                original_input
                if isinstance(original_input, str)
                else safe_serialize(original_input)
            )
            messages.append(
                {
                    "content": [{"text": message, "type": "text"}],
                    "role": "user",
                }
            )

        if len(messages) > 0:
            normalized_messages = normalize_message_roles(messages)
            client = sentry_sdk.get_client()
            scope = sentry_sdk.get_current_scope()
            messages_data = (
                truncate_and_annotate_messages(normalized_messages, span, scope)
                if not has_span_streaming_enabled(client.options)
                else normalized_messages
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
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    context: "agents.RunContextWrapper",
    agent: "agents.Agent",
    output: "Any" = None,
) -> None:
    # Add aggregated usage data from context_wrapper
    if hasattr(context, "usage"):
        _set_usage_data(span, context.usage)

    client = sentry_sdk.get_client()
    if has_data_collection_enabled(client.options):
        if client.options["data_collection"]["gen_ai"]["outputs"]:
            set_data_normalized(
                span, SPANDATA.GEN_AI_RESPONSE_TEXT, output, unpack=False
            )
    elif should_send_default_pii():
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, output, unpack=False)

    # Add conversation ID from agent
    conv_id = getattr(agent, "_sentry_conversation_id", None)
    if conv_id:
        if isinstance(span, StreamedSpan):
            span.set_attribute(SPANDATA.GEN_AI_CONVERSATION_ID, conv_id)
        else:
            span.set_data(SPANDATA.GEN_AI_CONVERSATION_ID, conv_id)

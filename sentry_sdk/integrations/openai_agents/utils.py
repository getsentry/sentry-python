import json
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai._openai_responses_api import (
    _get_system_instructions,
    _is_system_instruction,
    _transform_system_instructions,
)
from sentry_sdk.ai.utils import (
    GEN_AI_ALLOWED_MESSAGE_ROLES,
    normalize_message_role,
    normalize_message_roles,
    set_data_normalized,
)
from sentry_sdk.consts import SPANDATA
from sentry_sdk.traces import Span
from sentry_sdk.utils import (
    event_from_exception,
    safe_serialize,
)

if TYPE_CHECKING:
    from typing import Any

    from agents import TResponseInputItem, Usage

    from sentry_sdk._types import TextPart


def _capture_exception(exc: "Any") -> None:
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "openai_agents", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _set_usage_data(span: "Span", usage: "Usage") -> None:
    span.set_attribute(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens)
    span.set_attribute(
        SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
        usage.input_tokens_details.cached_tokens,
    )
    span.set_attribute(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens)
    span.set_attribute(
        SPANDATA.GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
        usage.output_tokens_details.reasoning_tokens,
    )
    span.set_attribute(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_tokens)


def _set_input_data(
    span: "Span",
    get_response_kwargs: "dict[str, Any]",
) -> None:
    client = sentry_sdk.get_client()
    if not client.options["data_collection"]["gen_ai"]["inputs"]:
        return

    request_messages = []

    messages: "str | list[TResponseInputItem]" = get_response_kwargs.get("input", [])

    instructions_text_parts: "list[TextPart]" = []
    explicit_instructions = get_response_kwargs.get("system_instructions")
    if explicit_instructions is not None:
        instructions_text_parts.append(
            {
                "type": "text",
                "content": explicit_instructions,
            }
        )

    system_instructions = _get_system_instructions(messages)

    instructions_text_parts += _transform_system_instructions(system_instructions)

    if len(instructions_text_parts) > 0:
        span.set_attribute(
            SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
            json.dumps(instructions_text_parts),
        )

    non_system_messages = [
        message
        for message in messages
        if not _is_system_instruction(message)  # type: ignore[arg-type]
    ]
    for message in non_system_messages:
        if "role" in message:
            normalized_role = normalize_message_role(message.get("role"))  # type: ignore
            content = message.get("content")  # type: ignore
            request_messages.append(
                {
                    "role": normalized_role,
                    "content": (
                        [{"type": "text", "text": content}]
                        if isinstance(content, str)
                        else content
                    ),
                }
            )
        else:
            if message.get("type") == "function_call":  # type: ignore
                request_messages.append(
                    {
                        "role": GEN_AI_ALLOWED_MESSAGE_ROLES.ASSISTANT,
                        "content": [message],
                    }
                )
            elif message.get("type") == "function_call_output":  # type: ignore
                request_messages.append(
                    {
                        "role": GEN_AI_ALLOWED_MESSAGE_ROLES.TOOL,
                        "content": [message],
                    }
                )

    normalized_messages = normalize_message_roles(request_messages)
    set_data_normalized(
        span,
        SPANDATA.GEN_AI_REQUEST_MESSAGES,
        normalized_messages,
        unpack=False,
    )


def _set_output_data(span: "Span", result: "Any") -> None:
    client = sentry_sdk.get_client()

    if not client.options["data_collection"]["gen_ai"]["outputs"]:
        return

    output_messages: "dict[str, list[Any]]" = {
        "response": [],
        "tool": [],
    }

    for output in result.output:
        if output.type == "function_call":
            output_messages["tool"].append(output.dict())
        elif output.type == "message":
            for output_message in output.content:
                try:
                    output_messages["response"].append(output_message.text)
                except AttributeError:
                    # Unknown output message type, just return the json
                    output_messages["response"].append(output_message.dict())

    if len(output_messages["tool"]) > 0:
        span.set_attribute(
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
            safe_serialize(output_messages["tool"]),
        )

    if len(output_messages["response"]) > 0:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, output_messages["response"]
        )

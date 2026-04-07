import json

import sentry_sdk
from contextvars import ContextVar
from sentry_sdk.consts import SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import set_span_errored
from sentry_sdk.utils import event_from_exception, safe_serialize

from sentry_sdk.ai.utils import (
    normalize_message_roles,
    set_data_normalized,
    truncate_and_annotate_messages,
)
from sentry_sdk._types import BLOB_DATA_SUBSTITUTE
from sentry_sdk.ai.utils import get_modality_from_mime_type


from .consts import DATA_URL_BASE64_REGEX

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, List, Dict
    from pydantic_ai.messages import ModelMessage, SystemPromptPart  # type: ignore
    from sentry_sdk._types import TextPart as SentryTextPart


try:
    from pydantic_ai.messages import (
        BaseToolCallPart,
        BaseToolReturnPart,
        SystemPromptPart,
        TextPart,
        ThinkingPart,
        BinaryContent,
        ImageUrl,
    )
except ImportError:
    # Fallback if these classes are not available
    BaseToolCallPart = None
    BaseToolReturnPart = None
    SystemPromptPart = None
    TextPart = None
    ThinkingPart = None
    BinaryContent = None
    ImageUrl = None


# Store the current agent context in a contextvar for re-entrant safety
# Using a list as a stack to support nested agent calls
_agent_context_stack: "ContextVar[list[dict[str, Any]]]" = ContextVar(
    "pydantic_ai_agent_context_stack", default=[]
)


def push_agent(agent: "Any", is_streaming: bool = False) -> None:
    """Push an agent context onto the stack along with its streaming flag."""
    stack = _agent_context_stack.get().copy()
    stack.append({"agent": agent, "is_streaming": is_streaming})
    _agent_context_stack.set(stack)


def pop_agent() -> None:
    """Pop an agent context from the stack."""
    stack = _agent_context_stack.get().copy()
    if stack:
        stack.pop()
    _agent_context_stack.set(stack)


def get_current_agent() -> "Any":
    """Get the current agent from the contextvar stack."""
    stack = _agent_context_stack.get()
    if stack:
        return stack[-1]["agent"]
    return None


def get_is_streaming() -> bool:
    """Get the streaming flag from the contextvar stack."""
    stack = _agent_context_stack.get()
    if stack:
        return stack[-1].get("is_streaming", False)
    return False


def _should_send_prompts() -> bool:
    """
    Check if prompts should be sent to Sentry.

    This checks both send_default_pii and the include_prompts integration setting.
    """
    if not should_send_default_pii():
        return False

    from . import PydanticAIIntegration

    # Get the integration instance from the client
    integration = sentry_sdk.get_client().get_integration(PydanticAIIntegration)

    if integration is None:
        return False

    return getattr(integration, "include_prompts", False)


def _set_agent_data(span: "sentry_sdk.tracing.Span", agent: "Any") -> None:
    """Set agent-related data on a span.

    Args:
        span: The span to set data on
        agent: Agent object (can be None, will try to get from contextvar if not provided)
    """
    # Extract agent name from agent object or contextvar
    agent_obj = agent
    if not agent_obj:
        # Try to get from contextvar
        agent_obj = get_current_agent()

    if agent_obj and hasattr(agent_obj, "name") and agent_obj.name:
        span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent_obj.name)


def _get_model_name(model_obj: "Any") -> "Optional[str]":
    """Extract model name from a model object.

    Args:
        model_obj: Model object to extract name from

    Returns:
        Model name string or None if not found
    """
    if not model_obj:
        return None

    if hasattr(model_obj, "model_name"):
        return model_obj.model_name
    elif hasattr(model_obj, "name"):
        try:
            return model_obj.name()
        except Exception:
            return str(model_obj)
    elif isinstance(model_obj, str):
        return model_obj
    else:
        return str(model_obj)


def _set_model_data(
    span: "sentry_sdk.tracing.Span", model: "Any", model_settings: "Any"
) -> None:
    """Set model-related data on a span.

    Args:
        span: The span to set data on
        model: Model object (can be None, will try to get from agent if not provided)
        model_settings: Model settings (can be None, will try to get from agent if not provided)
    """
    # Try to get agent from contextvar if we need it
    agent_obj = get_current_agent()

    # Extract model information
    model_obj = model
    if not model_obj and agent_obj and hasattr(agent_obj, "model"):
        model_obj = agent_obj.model

    if model_obj:
        # Set system from model
        if hasattr(model_obj, "system"):
            span.set_data(SPANDATA.GEN_AI_SYSTEM, model_obj.system)

        # Set model name
        model_name = _get_model_name(model_obj)
        if model_name:
            span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_name)

    # Extract model settings
    settings = model_settings
    if not settings and agent_obj and hasattr(agent_obj, "model_settings"):
        settings = agent_obj.model_settings

    if settings:
        settings_map = {
            "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
            "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
        }

        # ModelSettings is a TypedDict (dict at runtime), so use dict access
        if isinstance(settings, dict):
            for setting_name, spandata_key in settings_map.items():
                value = settings.get(setting_name)
                if value is not None:
                    span.set_data(spandata_key, value)
        else:
            # Fallback for object-style settings
            for setting_name, spandata_key in settings_map.items():
                if hasattr(settings, setting_name):
                    value = getattr(settings, setting_name)
                    if value is not None:
                        span.set_data(spandata_key, value)


def _set_available_tools(span: "sentry_sdk.tracing.Span", agent: "Any") -> None:
    """Set available tools data on a span from an agent's function toolset.

    Args:
        span: The span to set data on
        agent: Agent object with _function_toolset attribute
    """
    if not agent or not hasattr(agent, "_function_toolset"):
        return

    try:
        tools = []
        # Get tools from the function toolset
        if hasattr(agent._function_toolset, "tools"):
            for tool_name, tool in agent._function_toolset.tools.items():
                tool_info = {"name": tool_name}

                # Add description from function_schema if available
                if hasattr(tool, "function_schema"):
                    schema = tool.function_schema
                    if getattr(schema, "description", None):
                        tool_info["description"] = schema.description

                    # Add parameters from json_schema
                    if getattr(schema, "json_schema", None):
                        tool_info["parameters"] = schema.json_schema

                tools.append(tool_info)

        if tools:
            span.set_data(
                SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, safe_serialize(tools)
            )
    except Exception:
        # If we can't extract tools, just skip it
        pass


def _capture_exception(exc: "Any", handled: bool = False) -> None:
    set_span_errored()

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "pydantic_ai", "handled": handled},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _transform_system_instructions(
    permanent_instructions: "list[SystemPromptPart]",
    current_instructions: "list[str]",
) -> "list[SentryTextPart]":
    text_parts: "list[SentryTextPart]" = [
        {
            "type": "text",
            "content": instruction.content,
        }
        for instruction in permanent_instructions
    ]

    text_parts.extend(
        {
            "type": "text",
            "content": instruction,
        }
        for instruction in current_instructions
    )

    return text_parts


def _get_system_instructions(
    messages: "list[ModelMessage]",
) -> "tuple[list[SystemPromptPart], list[str]]":
    permanent_instructions = []
    current_instructions = []

    for msg in messages:
        if hasattr(msg, "parts"):
            for part in msg.parts:
                if SystemPromptPart and isinstance(part, SystemPromptPart):
                    permanent_instructions.append(part)

        if hasattr(msg, "instructions") and msg.instructions is not None:
            current_instructions.append(msg.instructions)

    return permanent_instructions, current_instructions


def _serialize_image_url_item(item: "Any") -> "Dict[str, Any]":
    """Serialize an ImageUrl content item for span data.

    For data URLs containing base64-encoded images, the content is redacted.
    For regular HTTP URLs, the URL string is preserved.
    """
    url = str(item.url)
    data_url_match = DATA_URL_BASE64_REGEX.match(url)

    if data_url_match:
        return {
            "type": "image",
            "content": BLOB_DATA_SUBSTITUTE,
        }

    return {
        "type": "image",
        "content": url,
    }


def _serialize_binary_content_item(item: "Any") -> "Dict[str, Any]":
    """Serialize a BinaryContent item for span data, redacting the blob data."""
    return {
        "type": "blob",
        "modality": get_modality_from_mime_type(item.media_type),
        "mime_type": item.media_type,
        "content": BLOB_DATA_SUBSTITUTE,
    }


def _set_input_messages(span: "sentry_sdk.tracing.Span", messages: "Any") -> None:
    """Set input messages data on a span."""
    if not _should_send_prompts():
        return

    if not messages:
        return

    permanent_instructions, current_instructions = _get_system_instructions(messages)
    if len(permanent_instructions) > 0 or len(current_instructions) > 0:
        span.set_data(
            SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
            json.dumps(
                _transform_system_instructions(
                    permanent_instructions, current_instructions
                )
            ),
        )

    try:
        formatted_messages = []

        for msg in messages:
            if hasattr(msg, "parts"):
                for part in msg.parts:
                    role = "user"
                    # Use isinstance checks with proper base classes
                    if SystemPromptPart and isinstance(part, SystemPromptPart):
                        continue
                    elif (
                        (TextPart and isinstance(part, TextPart))
                        or (ThinkingPart and isinstance(part, ThinkingPart))
                        or (BaseToolCallPart and isinstance(part, BaseToolCallPart))
                    ):
                        role = "assistant"
                    elif BaseToolReturnPart and isinstance(part, BaseToolReturnPart):
                        role = "tool"

                    content: "List[Dict[str, Any] | str]" = []
                    tool_calls = None
                    tool_call_id = None

                    # Handle ToolCallPart (assistant requesting tool use)
                    if BaseToolCallPart and isinstance(part, BaseToolCallPart):
                        tool_call_data = {}
                        if hasattr(part, "tool_name"):
                            tool_call_data["name"] = part.tool_name
                        if hasattr(part, "args"):
                            tool_call_data["arguments"] = safe_serialize(part.args)
                        if tool_call_data:
                            tool_calls = [tool_call_data]
                    # Handle ToolReturnPart (tool result)
                    elif BaseToolReturnPart and isinstance(part, BaseToolReturnPart):
                        if hasattr(part, "tool_name"):
                            tool_call_id = part.tool_name
                        if hasattr(part, "content"):
                            content.append({"type": "text", "text": str(part.content)})
                    # Handle regular content
                    elif hasattr(part, "content"):
                        if isinstance(part.content, str):
                            content.append({"type": "text", "text": part.content})
                        elif isinstance(part.content, list):
                            for item in part.content:
                                if isinstance(item, str):
                                    content.append({"type": "text", "text": item})
                                elif ImageUrl and isinstance(item, ImageUrl):
                                    content.append(_serialize_image_url_item(item))
                                elif BinaryContent and isinstance(item, BinaryContent):
                                    content.append(_serialize_binary_content_item(item))
                                else:
                                    content.append(safe_serialize(item))
                        else:
                            content.append({"type": "text", "text": str(part.content)})
                    # Add message if we have content or tool calls
                    if content or tool_calls:
                        message: "Dict[str, Any]" = {"role": role}
                        if content:
                            message["content"] = content
                        if tool_calls:
                            message["tool_calls"] = tool_calls
                        if tool_call_id:
                            message["tool_call_id"] = tool_call_id
                        formatted_messages.append(message)

        if formatted_messages:
            normalized_messages = normalize_message_roles(formatted_messages)
            scope = sentry_sdk.get_current_scope()
            messages_data = truncate_and_annotate_messages(
                normalized_messages, span, scope
            )
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages_data, unpack=False
            )
    except Exception:
        # If we fail to format messages, just skip it
        pass

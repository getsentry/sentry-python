"""Typed accessors for reading data off pydantic-ai objects.

This module concentrates reads of pydantic-ai object internals (including
private attributes and version-dependent shapes) so that upstream library
changes are absorbed here rather than throughout the integration. The one
exception is control-flow state read at the patch points themselves (e.g.
ModelRequestNode._did_stream in patches/graph_nodes.py and Tool.tool_def in
patches/tools.py); everything else consumes the plain data structures
returned here.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sentry_sdk._types import BLOB_DATA_SUBSTITUTE
from sentry_sdk.ai.consts import DATA_URL_BASE64_REGEX
from sentry_sdk.ai.utils import get_modality_from_mime_type
from sentry_sdk.utils import safe_serialize

try:
    from pydantic_ai.messages import (
        BaseToolCallPart,
        BaseToolReturnPart,
        BinaryContent,
        ImageUrl,
        SystemPromptPart,
        TextPart,
        ThinkingPart,
    )
except ImportError:
    # Fallback if these classes are not available
    BaseToolCallPart = None  # type: ignore[misc,assignment]
    BaseToolReturnPart = None  # type: ignore[misc,assignment]
    BinaryContent = None  # type: ignore[misc,assignment]
    ImageUrl = None  # type: ignore[misc,assignment]
    SystemPromptPart = None  # type: ignore[misc,assignment]
    TextPart = None  # type: ignore[misc,assignment]
    ThinkingPart = None  # type: ignore[misc,assignment]

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional

    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.messages import SystemPromptPart as SystemPromptPartType

    from sentry_sdk import _types


@dataclass
class ModelInfo:
    name: "Optional[str]" = None
    system: "Optional[str]" = None
    settings: "Dict[str, Any]" = field(default_factory=dict)


@dataclass
class UsageInfo:
    input_tokens: "Optional[int]" = None
    cache_read_tokens: "Optional[int]" = None
    cache_write_tokens: "Optional[int]" = None
    output_tokens: "Optional[int]" = None
    total_tokens: "Optional[int]" = None


# Model settings that get mirrored onto spans; values are read with dict
# access first because ModelSettings is a TypedDict (dict at runtime).
MODEL_SETTING_NAMES = (
    "max_tokens",
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
)


def get_model_name(model_obj: "Any") -> "Optional[str]":
    """Extract model name from a model object."""
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


def extract_model_settings(settings: "Any") -> "Dict[str, Any]":
    """Extract known model settings as a plain dict of non-None values."""
    extracted: "Dict[str, Any]" = {}
    if not settings:
        return extracted

    for setting_name in MODEL_SETTING_NAMES:
        if isinstance(settings, dict):
            value = settings.get(setting_name)
        else:
            # Fallback for object-style settings
            value = getattr(settings, setting_name, None)
        if value is not None:
            extracted[setting_name] = value

    return extracted


def extract_model_info(
    model: "Any", model_settings: "Any", agent: "Any"
) -> "ModelInfo":
    """Extract model name, provider system, and settings.

    Falls back to the agent's model and model_settings when the explicit
    arguments are not provided.
    """
    model_obj = model
    if not model_obj and agent and hasattr(agent, "model"):
        model_obj = agent.model

    info = ModelInfo()
    if model_obj:
        info.system = getattr(model_obj, "system", None)
        info.name = get_model_name(model_obj)

    settings = model_settings
    if not settings and agent and hasattr(agent, "model_settings"):
        settings = agent.model_settings
    info.settings = extract_model_settings(settings)

    return info


def extract_agent_name(agent: "Any") -> "Optional[str]":
    if agent and hasattr(agent, "name") and agent.name:
        return agent.name
    return None


def extract_available_tools(agent: "Any") -> "Optional[List[Dict[str, Any]]]":
    """Extract the agent's available tool definitions from its function toolset."""
    if not agent or not hasattr(agent, "_function_toolset"):
        return None

    try:
        tools = []
        if hasattr(agent._function_toolset, "tools"):
            for tool_name, tool in agent._function_toolset.tools.items():
                tool_info: "Dict[str, Any]" = {"name": tool_name}

                if hasattr(tool, "function_schema"):
                    schema = tool.function_schema
                    if getattr(schema, "description", None):
                        tool_info["description"] = schema.description

                    if getattr(schema, "json_schema", None):
                        tool_info["parameters"] = schema.json_schema

                tools.append(tool_info)

        return tools or None
    except Exception:
        # If we can't extract tools, just skip it
        return None


def extract_usage(usage: "Any") -> "Optional[UsageInfo]":
    """Extract token usage counts.

    Works with both RequestUsage (single request) and RunUsage (agent run)
    objects from pydantic-ai; note the library uses cache_read_tokens /
    cache_write_tokens naming.
    """
    if usage is None:
        return None

    return UsageInfo(
        input_tokens=getattr(usage, "input_tokens", None),
        cache_read_tokens=getattr(usage, "cache_read_tokens", None),
        cache_write_tokens=getattr(usage, "cache_write_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )


def serialize_image_url_item(item: "Any") -> "Dict[str, Any]":
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


def serialize_binary_content_item(item: "Any") -> "Dict[str, Any]":
    """Serialize a BinaryContent item for span data, redacting the blob data."""
    return {
        "type": "blob",
        "modality": get_modality_from_mime_type(item.media_type),
        "mime_type": item.media_type,
        "content": BLOB_DATA_SUBSTITUTE,
    }


def _collect_system_instructions(
    messages: "List[ModelMessage]",
) -> "tuple[List[SystemPromptPartType], List[str]]":
    permanent_instructions = []
    current_instructions = []

    for msg in messages:
        if hasattr(msg, "parts"):
            for part in msg.parts:
                if SystemPromptPart is not None and isinstance(part, SystemPromptPart):
                    permanent_instructions.append(part)

        if hasattr(msg, "instructions") and msg.instructions is not None:
            current_instructions.append(msg.instructions)

    return permanent_instructions, current_instructions


def extract_system_instructions(
    messages: "List[ModelMessage]",
) -> "List[_types.TextPart]":
    """Extract permanent and per-request system instructions as text parts."""
    permanent_instructions, current_instructions = _collect_system_instructions(
        messages
    )

    text_parts: "List[_types.TextPart]" = [
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


def extract_request_messages(messages: "Any") -> "List[Dict[str, Any]]":
    """Extract a conversation history as gen_ai-format message dicts.

    System prompt parts are skipped; they are reported separately via
    extract_system_instructions().
    """
    formatted_messages = []

    for msg in messages:
        if not hasattr(msg, "parts"):
            continue

        for part in msg.parts:
            role = "user"
            # Use isinstance checks with proper base classes
            if SystemPromptPart is not None and isinstance(part, SystemPromptPart):
                continue
            elif (
                (TextPart is not None and isinstance(part, TextPart))
                or (ThinkingPart is not None and isinstance(part, ThinkingPart))
                or (BaseToolCallPart is not None and isinstance(part, BaseToolCallPart))
            ):
                role = "assistant"
            elif BaseToolReturnPart is not None and isinstance(
                part, BaseToolReturnPart
            ):
                role = "tool"

            content: "List[Dict[str, Any] | str]" = []
            tool_calls = None
            tool_call_id = None

            # Handle ToolCallPart (assistant requesting tool use)
            if BaseToolCallPart is not None and isinstance(part, BaseToolCallPart):
                tool_call_data = {}
                if hasattr(part, "tool_name"):
                    tool_call_data["name"] = part.tool_name
                if hasattr(part, "args"):
                    tool_call_data["arguments"] = safe_serialize(part.args)
                if tool_call_data:
                    tool_calls = [tool_call_data]
            # Handle ToolReturnPart (tool result)
            elif BaseToolReturnPart is not None and isinstance(
                part, BaseToolReturnPart
            ):
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
                        elif ImageUrl is not None and isinstance(item, ImageUrl):
                            content.append(serialize_image_url_item(item))
                        elif BinaryContent is not None and isinstance(
                            item, BinaryContent
                        ):
                            content.append(serialize_binary_content_item(item))
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

    return formatted_messages


def extract_response_parts(
    response: "ModelResponse",
) -> "List[_types.TextPart | _types.ReasoningPart | _types.ToolCallPart]":
    """Extract a model response's parts as gen_ai-format output parts."""
    parts: "List[_types.TextPart | _types.ReasoningPart | _types.ToolCallPart]" = []

    if not hasattr(response, "parts"):
        return parts

    for part in response.parts:
        if (
            TextPart is not None
            and isinstance(part, TextPart)
            and hasattr(part, "content")
        ):
            parts.append({"type": "text", "content": part.content})

        elif ThinkingPart is not None and isinstance(part, ThinkingPart):
            parts.append(
                {
                    "type": "reasoning",
                    "content": part.content,
                }
            )

        elif BaseToolCallPart is not None and isinstance(part, BaseToolCallPart):
            tool_part: "_types.ToolCallPart" = {"type": "tool_call"}
            if hasattr(part, "tool_name"):
                tool_part["name"] = part.tool_name
            if hasattr(part, "args"):
                tool_part["arguments"] = safe_serialize(part.args)
            parts.append(tool_part)

    return parts


def extract_agent_prompt_messages(
    agent: "Any", user_prompt: "Any"
) -> "List[Dict[str, Any]]":
    """Extract an agent's static system prompts plus the user prompt as
    gen_ai-format message dicts."""
    messages: "List[Dict[str, Any]]" = []

    # Add system prompts (both system_prompt and instructions)
    system_texts = []

    if agent:
        system_prompts = getattr(agent, "_system_prompts", None) or []
        for prompt in system_prompts:
            if isinstance(prompt, str):
                system_texts.append(prompt)

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

    for system_text in system_texts:
        messages.append(
            {
                "content": [{"text": system_text, "type": "text"}],
                "role": "system",
            }
        )

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
                elif ImageUrl is not None and isinstance(item, ImageUrl):
                    content.append(serialize_image_url_item(item))
                elif BinaryContent is not None and isinstance(item, BinaryContent):
                    content.append(serialize_binary_content_item(item))
            if content:
                messages.append(
                    {
                        "content": content,
                        "role": "user",
                    }
                )

    return messages


def extract_response_model_name(result: "Any") -> "Optional[str]":
    """Extract the responding model's name from an agent run result."""
    try:
        # Accessing .response can itself raise (e.g. AgentRunResult raises
        # ValueError when the run produced no ModelResponse), so the access
        # must live inside the try block.
        response = result.response
        if hasattr(response, "model_name") and response.model_name:
            return response.model_name
    except Exception:
        # If response access fails, continue without the model name
        pass
    return None


def extract_graph_request_data(node: "Any", ctx: "Any") -> "tuple[List[Any], Any, Any]":
    """Extract (messages, model, model_settings) from a ModelRequestNode and
    its graph context, for the legacy (pre-hooks) instrumentation path."""
    model = None
    model_settings = None
    if hasattr(ctx, "deps"):
        model = getattr(ctx.deps, "model", None)
        model_settings = getattr(ctx.deps, "model_settings", None)

    # Build full message list: history + current request
    messages = []
    if hasattr(ctx, "state") and hasattr(ctx.state, "message_history"):
        messages.extend(ctx.state.message_history)

    current_request = getattr(node, "request", None)
    if current_request:
        messages.append(current_request)

    return messages, model, model_settings


def extract_tool_call_args(call: "Any") -> "Dict[str, Any]":
    """Extract a tool call's arguments as a dict."""
    try:
        return call.args_as_dict()
    except Exception:
        return call.args if isinstance(call.args, dict) else {}

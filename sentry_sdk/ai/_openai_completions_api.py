from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union

    from openai.types.chat import (
        ChatCompletionContentPartParam,
        ChatCompletionMessageParam,
        ChatCompletionSystemMessageParam,
        ChatCompletionToolUnionParam,
    )

    from sentry_sdk._types import TextPart, ToolDefinition


def _is_system_instruction(message: "ChatCompletionMessageParam") -> bool:
    return isinstance(message, dict) and message.get("role") == "system"


def _get_system_instructions(
    messages: "Iterable[ChatCompletionMessageParam]",
) -> "list[ChatCompletionMessageParam]":
    if not isinstance(messages, Iterable):
        return []

    return [message for message in messages if _is_system_instruction(message)]


def _get_text_items(
    content: "Union[str, Iterable[ChatCompletionContentPartParam]]",
) -> "list[str]":
    if isinstance(content, str):
        return [content]

    if not isinstance(content, Iterable):
        return []

    text_items = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text", None)
            if text is not None:
                text_items.append(text)

    return text_items


def _transform_system_instructions(
    system_instructions: "list[ChatCompletionSystemMessageParam]",
) -> "list[TextPart]":
    instruction_text_parts: "list[TextPart]" = []

    for instruction in system_instructions:
        if not isinstance(instruction, dict):
            continue

        content = instruction.get("content")
        if content is None:
            continue

        text_parts: "list[TextPart]" = [
            {"type": "text", "content": text} for text in _get_text_items(content)
        ]
        instruction_text_parts += text_parts

    return instruction_text_parts


def _transform_tool_definitions(
    tools: "Iterable[ChatCompletionToolUnionParam]",
) -> "list[ToolDefinition]":
    """
    Transform tool definitions to the schema used by the "gen_ai.tool.definitions" attribute.
    """
    if not isinstance(tools, Iterable):
        return []

    tool_definitions = []
    for tool in tools:
        if not isinstance(tool, dict) or "type" not in tool:
            continue

        if tool["type"] == "function":
            tool_definition: ToolDefinition = {
                "type": "function",
            }

            if "function" not in tool:
                tool_definitions.append(tool_definition)
                continue

            if "name" in tool["function"]:
                tool_definition["name"] = tool["function"]["name"]

            if "description" in tool["function"]:
                tool_definition["description"] = tool["function"]["description"]

            if "parameters" in tool["function"]:
                tool_definition["parameters"] = tool["function"]["parameters"]

            tool_definitions.append(tool_definition)
            continue

        if tool["type"] == "custom":
            tool_definition = {
                "type": "custom",
            }

            if "name" in tool["custom"]:
                tool_definition["name"] = tool["custom"]["name"]

            if "description" in tool["custom"]:
                tool_definition["description"] = tool["custom"]["description"]

            tool_definitions.append(tool_definition)
            continue

    return tool_definitions

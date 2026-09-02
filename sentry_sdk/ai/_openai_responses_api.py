from typing import TYPE_CHECKING, Iterable, cast

if TYPE_CHECKING:
    from typing import Iterable, Union

    from openai.types.responses import (
        ResponseInputItemParam,
        ResponseInputParam,
        ResponseInputTextParam,
        ToolParam,
    )
    from openai.types.responses.easy_input_message_param import EasyInputMessageParam
    from openai.types.responses.response_input_item_param import Message

    from sentry_sdk._types import TextPart, ToolDefinition


def _is_system_instruction(message: "ResponseInputItemParam") -> bool:
    if not isinstance(message, dict) or not message.get("role") == "system":
        return False

    return "type" not in message or message["type"] == "message"


def _get_system_instructions(
    messages: "Union[str, ResponseInputParam]",
) -> "list[ResponseInputItemParam]":
    if not isinstance(messages, list):
        return []

    return [message for message in messages if _is_system_instruction(message)]


def _transform_system_instructions(
    system_instructions: "list[Union[EasyInputMessageParam, Message]]",
) -> "list[TextPart]":
    instruction_text_parts: "list[TextPart]" = []

    for instruction in system_instructions:
        if not isinstance(instruction, dict):
            continue

        content = instruction.get("content")
        if content is None:
            continue

        if isinstance(content, str):
            instruction_text_parts.append({"type": "text", "content": content})
            continue

        if not isinstance(content, Iterable):
            continue

        for part in content:
            if not isinstance(part, dict) or part.get("type") != "input_text":
                continue

            text = cast("ResponseInputTextParam", part).get("text", None)
            if text is not None:
                instruction_text_parts.append({"type": "text", "content": text})

    return instruction_text_parts


def _transform_tool_definitions(tools: "Iterable[ToolParam]") -> "list[ToolDefinition]":
    """
    Transform tool definitions to the schema used by the "gen_ai.tool.definitions" attribute.
    Includes special handling for tools where the type includes a name, description or parameters.
    """
    if not isinstance(tools, Iterable):
        return []

    tool_definitions = []
    for tool in tools:
        if not isinstance(tool, dict) or "type" not in tool:
            continue

        if tool["type"] == "function":
            tool_definition: "ToolDefinition" = {
                "type": "function",
            }

            if "name" in tool:
                tool_definition["name"] = tool["name"]

            if "description" in tool and tool["description"] is not None:
                tool_definition["description"] = tool["description"]

            if "parameters" in tool and tool["parameters"] is not None:
                tool_definition["parameters"] = tool["parameters"]

            tool_definitions.append(tool_definition)
            continue

        if tool["type"] == "custom":
            tool_definition = {
                "type": "custom",
            }

            if "name" in tool:
                tool_definition["name"] = tool["name"]

            if "description" in tool:
                tool_definition["description"] = tool["description"]

            tool_definitions.append(tool_definition)
            continue

        tool_definitions.append({"type": tool["type"]})

    return tool_definitions

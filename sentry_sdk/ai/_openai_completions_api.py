from collections.abc import Iterable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentry_sdk._types import TextPart
    from typing import Union

    from openai.types.chat import (
        ChatCompletionMessageParam,
        ChatCompletionSystemMessageParam,
        ChatCompletionContentPartParam,
    )


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

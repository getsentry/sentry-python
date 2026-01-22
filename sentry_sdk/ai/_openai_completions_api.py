from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types.chat import (
        ChatCompletionMessageParam,
        ChatCompletionSystemMessageParam,
    )
    from typing import Iterable

    from sentry_sdk._types import TextPart


def _is_system_instruction(message: "ChatCompletionMessageParam"):
    return isinstance(message, dict) and message.get("role") == "system"


def _get_system_instructions(
    messages: "Iterable[ChatCompletionMessageParam]",
) -> "list[ChatCompletionSystemMessageParam]":
    system_instructions = []

    for message in messages:
        if _is_system_instruction(message):
            system_instructions.append(message)

    return system_instructions


def _transform_system_instructions(
    system_instructions: "list[ChatCompletionSystemMessageParam]",
) -> "list[TextPart]":
    instruction_text_parts = []

    for instruction in system_instructions:
        if not isinstance(instruction, dict):
            continue

        content = instruction.get("content")

        if isinstance(content, str):
            instruction_text_parts.append({"type": "text", "content": content})

        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        instruction_text_parts.append({"type": "text", "content": text})

    return instruction_text_parts

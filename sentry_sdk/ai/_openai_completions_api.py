from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types.chat import (
        ChatCompletionMessageParam,
        ChatCompletionSystemMessageParam,
    )
    from typing import Iterable


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

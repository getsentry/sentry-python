from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union

    from openai.types.responses import ResponseInputParam, ResponseInputItemParam


def _is_system_instruction(message: "ResponseInputItemParam") -> bool:
    return (
        isinstance(message, dict)
        and message.get("type") == "message"
        and message.get("role") == "system"
    )


def _get_system_instructions(
    messages: "Union[str, ResponseInputParam]",
) -> "list[ResponseInputItemParam]":
    if isinstance(messages, str):
        return []

    system_instructions = []

    for message in messages:
        if _is_system_instruction(message):
            system_instructions.append(message)

    return system_instructions

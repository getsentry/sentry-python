from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types.chat import (
        ChatCompletionMessageParam,
        ChatCompletionSystemMessageParam,
    )
    from typing import Iterable, Union, TypeVar

    T = TypeVar("T")


def _get_system_instructions(
    messages: "Iterable[Union[T, str]]",
) -> "list[T]":
    system_messages = []

    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            system_messages.append(message)

    return system_messages

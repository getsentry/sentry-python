import inspect
import json
from copy import deepcopy
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple, Union

    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.utils import logger
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import has_span_streaming_enabled

MAX_GEN_AI_MESSAGE_BYTES = 20_000  # 20KB
# Maximum characters when only a single message is left after bytes truncation
MAX_SINGLE_MESSAGE_CONTENT_CHARS = 10_000


class GEN_AI_ALLOWED_MESSAGE_ROLES:
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


GEN_AI_MESSAGE_ROLE_REVERSE_MAPPING = {
    GEN_AI_ALLOWED_MESSAGE_ROLES.SYSTEM: ["system"],
    GEN_AI_ALLOWED_MESSAGE_ROLES.USER: ["user", "human"],
    GEN_AI_ALLOWED_MESSAGE_ROLES.ASSISTANT: ["assistant", "ai"],
    GEN_AI_ALLOWED_MESSAGE_ROLES.TOOL: ["tool", "tool_call"],
}

GEN_AI_MESSAGE_ROLE_MAPPING = {}
for target_role, source_roles in GEN_AI_MESSAGE_ROLE_REVERSE_MAPPING.items():
    for source_role in source_roles:
        GEN_AI_MESSAGE_ROLE_MAPPING[source_role] = target_role


def parse_data_uri(url: str) -> "Tuple[str, str]":
    """
    Parse a data URI and return (mime_type, content).

    Data URI format (RFC 2397): data:[<mediatype>][;base64],<data>

    Examples:
        data:image/jpeg;base64,/9j/4AAQ... → ("image/jpeg", "/9j/4AAQ...")
        data:text/plain,Hello → ("text/plain", "Hello")
        data:;base64,SGVsbG8= → ("", "SGVsbG8=")

    Raises:
        ValueError: If the URL is not a valid data URI (missing comma separator)
    """
    if "," not in url:
        raise ValueError("Invalid data URI: missing comma separator")

    header, content = url.split(",", 1)

    # Extract mime type from header
    # Format: "data:<mime>[;param1][;param2]..." e.g. "data:image/jpeg;base64"
    # Remove "data:" prefix, then take everything before the first semicolon
    if header.startswith("data:"):
        mime_part = header[5:]  # Remove "data:" prefix
    else:
        mime_part = header

    mime_type = mime_part.split(";")[0]

    return mime_type, content


def _normalize_data(data: "Any", unpack: bool = True) -> "Any":
    # convert pydantic data (e.g. OpenAI v1+) to json compatible format
    if hasattr(data, "model_dump"):
        # Check if it's a class (type) rather than an instance
        # Model classes can be passed as arguments (e.g., for schema definitions)
        if inspect.isclass(data):
            return f"<ClassType: {data.__name__}>"

        try:
            return _normalize_data(data.model_dump(), unpack=unpack)
        except Exception as e:
            logger.warning("Could not convert pydantic data to JSON: %s", e)
            return data if isinstance(data, (int, float, bool, str)) else str(data)

    if isinstance(data, list):
        if unpack and len(data) == 1:
            return _normalize_data(data[0], unpack=unpack)  # remove empty dimensions
        return list(_normalize_data(x, unpack=unpack) for x in data)

    if isinstance(data, dict):
        return {k: _normalize_data(v, unpack=unpack) for (k, v) in data.items()}

    return data if isinstance(data, (int, float, bool, str)) else str(data)


def set_data_normalized(
    span: "Union[Span, StreamedSpan]",
    key: str,
    value: "Any",
    unpack: bool = True,
) -> None:
    normalized = _normalize_data(value, unpack=unpack)
    if isinstance(normalized, (int, float, bool, str)):
        _set_span_data_attribute(span, key, normalized)
    else:
        _set_span_data_attribute(span, key, json.dumps(normalized))


def _set_span_data_attribute(
    span: "Union[Span, StreamedSpan]", key: str, value: "Any"
) -> None:
    if isinstance(span, StreamedSpan):
        span.set_attribute(key, value)
    else:
        span.set_data(key, value)


def normalize_message_role(role: str) -> str:
    """
    Normalize a message role to one of the 4 allowed gen_ai role values.
    Maps "ai" -> "assistant" and keeps other standard roles unchanged.
    """
    return GEN_AI_MESSAGE_ROLE_MAPPING.get(role, role)


def normalize_message_roles(messages: "list[dict[str, Any]]") -> "list[dict[str, Any]]":
    """
    Normalize roles in a list of messages to use standard gen_ai role values.
    Creates a deep copy to avoid modifying the original messages.
    """
    normalized_messages = []
    for message in messages:
        if not isinstance(message, dict):
            normalized_messages.append(message)
            continue
        normalized_message = message.copy()
        if "role" in message:
            normalized_message["role"] = normalize_message_role(message["role"])
        normalized_messages.append(normalized_message)

    return normalized_messages


def get_start_span_function() -> "Callable[..., Any]":
    if has_span_streaming_enabled(sentry_sdk.get_client().options):
        return sentry_sdk.traces.start_span

    current_span = sentry_sdk.get_current_span()
    if isinstance(current_span, StreamedSpan):
        # mypy
        return sentry_sdk.traces.start_span

    transaction_exists = (
        current_span is not None and current_span.containing_transaction is not None
    )
    return sentry_sdk.start_span if transaction_exists else sentry_sdk.start_transaction


def _truncate_single_message_content_if_present(
    message: "Dict[str, Any]", max_chars: int
) -> "Dict[str, Any]":
    """
    Truncate a message's content to at most `max_chars` characters and append an
    ellipsis if truncation occurs.
    """
    if not isinstance(message, dict) or "content" not in message:
        return message
    content = message["content"]

    if isinstance(content, str):
        if len(content) <= max_chars:
            return message
        message["content"] = content[:max_chars] + "..."
        return message

    if isinstance(content, list):
        remaining = max_chars
        for item in content:
            if isinstance(item, dict) and "text" in item:
                text = item["text"]
                if isinstance(text, str):
                    if len(text) > remaining:
                        item["text"] = text[:remaining] + "..."
                        remaining = 0
                    else:
                        remaining -= len(text)
        return message

    return message


def _find_truncation_index(messages: "List[Dict[str, Any]]", max_bytes: int) -> int:
    """
    Find the index of the first message that would exceed the max bytes limit.
    Compute the individual message sizes, and return the index of the first message from the back
    of the list that would exceed the max bytes limit.
    """
    running_sum = 0
    for idx in range(len(messages) - 1, -1, -1):
        size = len(json.dumps(messages[idx], separators=(",", ":")).encode("utf-8"))
        running_sum += size
        if running_sum > max_bytes:
            return idx + 1

    return 0


def truncate_messages_by_size(
    messages: "List[Dict[str, Any]]",
    max_bytes: int = MAX_GEN_AI_MESSAGE_BYTES,
    max_single_message_chars: int = MAX_SINGLE_MESSAGE_CONTENT_CHARS,
) -> "Tuple[List[Dict[str, Any]], int]":
    """
    Returns a truncated messages list, consisting of
    - the last message, with its content truncated to `max_single_message_chars` characters,
      if the last message's size exceeds `max_bytes` bytes; otherwise,
    - the maximum number of messages, starting from the end of the `messages` list, whose total
      serialized size does not exceed `max_bytes` bytes.

    In the single message case, the serialized message size may exceed `max_bytes`, because
    truncation is based only on character count in that case.
    """
    serialized_json = json.dumps(messages, separators=(",", ":"))
    current_size = len(serialized_json.encode("utf-8"))

    if current_size <= max_bytes:
        return messages, 0

    truncation_index = _find_truncation_index(messages, max_bytes)
    if truncation_index < len(messages):
        truncated_messages = messages[truncation_index:]
    else:
        truncation_index = len(messages) - 1
        truncated_messages = messages[-1:]

    if len(truncated_messages) == 1:
        truncated_messages[0] = _truncate_single_message_content_if_present(
            deepcopy(truncated_messages[0]), max_chars=max_single_message_chars
        )

    return truncated_messages, truncation_index


def truncate_and_annotate_messages(
    messages: "Optional[List[Dict[str, Any]]]",
    span: "Any",
    scope: "Any",
    max_single_message_chars: int = MAX_SINGLE_MESSAGE_CONTENT_CHARS,
) -> "Optional[List[Dict[str, Any]]]":
    if not messages:
        return None

    truncated_message = _truncate_single_message_content_if_present(
        deepcopy(messages[-1]), max_chars=max_single_message_chars
    )
    if len(messages) > 1:
        scope._gen_ai_original_message_count[span.span_id] = len(messages)

    return [truncated_message]


def truncate_and_annotate_embedding_inputs(
    messages: "Optional[List[Dict[str, Any]]]",
    span: "Any",
    scope: "Any",
    max_bytes: int = MAX_GEN_AI_MESSAGE_BYTES,
) -> "Optional[List[Dict[str, Any]]]":
    if not messages:
        return None

    truncated_messages, removed_count = truncate_messages_by_size(messages, max_bytes)
    if removed_count > 0:
        scope._gen_ai_original_message_count[span.span_id] = len(messages)

    return truncated_messages


def set_conversation_id(conversation_id: str) -> None:
    """
    Set the conversation_id in the scope.
    """
    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id(conversation_id)

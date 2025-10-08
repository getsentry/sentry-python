import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional

from sentry_sdk.serializer import serialize
from sentry_sdk._types import AnnotatedValue

MAX_GEN_AI_MESSAGE_BYTES = 20_000  # 20KB


def truncate_messages_by_size(messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES):
    # type: (List[Dict[str, Any]], int) -> List[Dict[str, Any]]
    """
    Truncate messages by removing the oldest ones until the serialized size is within limits.
    If the last message is still too large, truncate its content instead of removing it entirely.

    This function prioritizes keeping the most recent messages while ensuring the total
    serialized size stays under the specified byte limit. It uses the Sentry serializer
    to get accurate size estimates that match what will actually be sent.

    Always preserves at least one message, even if content needs to be truncated.

    :param messages: List of message objects (typically with 'role' and 'content' keys)
    :param max_bytes: Maximum allowed size in bytes for the serialized messages
    :returns: Truncated list of messages that fits within the size limit
    """
    if not messages:
        return messages

    truncated_messages = list(messages)

    # First, remove older messages until we're under the limit or have only one message left
    while len(truncated_messages) > 1:
        serialized = serialize(
            truncated_messages, is_vars=False, max_value_length=round(max_bytes * 0.8)
        )
        serialized_json = json.dumps(serialized, separators=(",", ":"))
        current_size = len(serialized_json.encode("utf-8"))

        if current_size <= max_bytes:
            break

        truncated_messages.pop(0)  # Remove oldest message

    # If we still have one message but it's too large, truncate its content
    # This ensures we always preserve at least one message
    if len(truncated_messages) == 1:
        serialized = serialize(
            truncated_messages, is_vars=False, max_value_length=round(max_bytes * 0.8)
        )
        serialized_json = json.dumps(serialized, separators=(",", ":"))
        current_size = len(serialized_json.encode("utf-8"))

        if current_size > max_bytes:
            # Truncate the content of the last message
            last_message = truncated_messages[0].copy()
            content = last_message.get("content", "")

            if content and isinstance(content, str):
                last_message["content"] = content[: max_bytes * 0.8] + "..."
                truncated_messages[0] = last_message

    return truncated_messages


def serialize_gen_ai_messages(messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES):
    # type: (Optional[Any], int) -> Optional[str]
    """
    Serialize and truncate gen_ai messages for storage in spans.

    This function handles the complete workflow of:
    1. Truncating messages to fit within size limits (if not already done)
    2. Serializing them using Sentry's serializer (which processes AnnotatedValue for _meta)
    3. Converting to JSON string for storage

    :param messages: List of message objects, AnnotatedValue, or None
    :param max_bytes: Maximum allowed size in bytes for the serialized messages
    :returns: JSON string of serialized messages or None if input was None/empty
    """
    if not messages:
        return None

    if isinstance(messages, AnnotatedValue):
        serialized_messages = serialize(
            messages, is_vars=False, max_value_length=round(max_bytes * 0.8)
        )
        return json.dumps(serialized_messages, separators=(",", ":"))

    truncated_messages = truncate_messages_by_size(messages, max_bytes)
    if not truncated_messages:
        return None
    serialized_messages = serialize(
        truncated_messages, is_vars=False, max_value_length=round(max_bytes * 0.8)
    )

    return json.dumps(serialized_messages, separators=(",", ":"))


def truncate_and_serialize_messages(messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES):
    # type: (Optional[List[Dict[str, Any]]], int) -> Any
    """
    Truncate messages and return serialized string or AnnotatedValue for automatic _meta creation.

    This function handles truncation and always returns serialized JSON strings. When truncation
    occurs, it wraps the serialized string in an AnnotatedValue so that Sentry's serializer can
    automatically create the appropriate _meta structure.

    :param messages: List of message objects or None
    :param max_bytes: Maximum allowed size in bytes for the serialized messages
    :returns: JSON string, AnnotatedValue containing JSON string (if truncated), or None
    """
    if not messages:
        return None

    truncated_messages = truncate_messages_by_size(messages, max_bytes)
    if not truncated_messages:
        return None

    # Always serialize to JSON string
    serialized_json = serialize_gen_ai_messages(truncated_messages, max_bytes)
    if not serialized_json:
        return None

    original_count = len(messages)
    truncated_count = len(truncated_messages)

    # If truncation occurred, wrap the serialized string in AnnotatedValue for _meta
    if original_count != truncated_count:
        return AnnotatedValue(
            value=serialized_json,
            metadata={"len": original_count},
        )

    # No truncation, return plain serialized string
    return serialized_json

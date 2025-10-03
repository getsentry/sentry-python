import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional

try:
    from sentry_sdk.serializer import serialize
except ImportError:
    # Fallback for cases where sentry_sdk isn't fully importable
    def serialize(obj, **kwargs):
        # type: (Any, **Any) -> Any
        return obj


MAX_GEN_AI_MESSAGE_BYTES = 30_000  # 300KB


def truncate_messages_by_size(messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES):
    # type: (List[Dict[str, Any]], int) -> List[Dict[str, Any]]
    """
    Truncate messages by removing the oldest ones until the serialized size is within limits.

    This function prioritizes keeping the most recent messages while ensuring the total
    serialized size stays under the specified byte limit. It uses the Sentry serializer
    to get accurate size estimates that match what will actually be sent.

    :param messages: List of message objects (typically with 'role' and 'content' keys)
    :param max_bytes: Maximum allowed size in bytes for the serialized messages
    :returns: Truncated list of messages that fits within the size limit
    """
    if not messages:
        return messages

    truncated_messages = list(messages)

    while truncated_messages:
        serialized = serialize(truncated_messages, is_vars=False)
        serialized_json = json.dumps(serialized, separators=(",", ":"))
        current_size = len(serialized_json.encode("utf-8"))

        if current_size <= max_bytes:
            break

        truncated_messages.pop(0)

    return truncated_messages


def serialize_gen_ai_messages(messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES):
    # type: (Optional[List[Dict[str, Any]]], int) -> Optional[str]
    """
    Serialize and truncate gen_ai messages for storage in spans.

    This function handles the complete workflow of:
    1. Truncating messages to fit within size limits
    2. Serializing them using Sentry's serializer
    3. Converting to JSON string for storage

    :param messages: List of message objects or None
    :param max_bytes: Maximum allowed size in bytes for the serialized messages
    :returns: JSON string of serialized messages or None if input was None/empty
    """
    if not messages:
        return None
    truncated_messages = truncate_messages_by_size(messages, max_bytes)
    if not truncated_messages:
        return None
    serialized_messages = serialize(truncated_messages, is_vars=False)

    return json.dumps(serialized_messages, separators=(",", ":"))


def get_messages_metadata(original_messages, truncated_messages):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]]) -> Dict[str, Any]
    """
    Generate metadata about message truncation for debugging/monitoring.

    :param original_messages: The original list of messages
    :param truncated_messages: The truncated list of messages
    :returns: Dictionary with metadata about the truncation
    """
    original_count = len(original_messages) if original_messages else 0
    truncated_count = len(truncated_messages) if truncated_messages else 0

    metadata = {
        "original_count": original_count,
        "truncated_count": truncated_count,
        "messages_removed": original_count - truncated_count,
        "was_truncated": original_count != truncated_count,
    }

    return metadata


def truncate_and_serialize_messages(messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES):
    # type: (Optional[List[Dict[str, Any]]], int) -> Dict[str, Any]
    """
    One-stop function for gen_ai integrations to truncate and serialize messages.

    This is the main function that gen_ai integrations should use. It handles the
    complete workflow and returns both the serialized data and metadata.

    Example usage:
        from sentry_sdk.ai.message_utils import truncate_and_serialize_messages

        result = truncate_and_serialize_messages(messages)
        if result['serialized_data']:
            span.set_data('gen_ai.request.messages', result['serialized_data'])
        if result['metadata']['was_truncated']:
            # Log warning about truncation if desired
            pass

    :param messages: List of message objects or None
    :param max_bytes: Maximum allowed size in bytes for the serialized messages
    :returns: Dictionary containing 'serialized_data', 'metadata', and 'original_size'
    """
    if not messages:
        return {
            "serialized_data": None,
            "metadata": get_messages_metadata([], []),
            "original_size": 0,
        }

    original_serialized = serialize(messages, is_vars=False)
    original_json = json.dumps(original_serialized, separators=(",", ":"))
    original_size = len(original_json.encode("utf-8"))

    truncated_messages = truncate_messages_by_size(messages, max_bytes)
    serialized_data = serialize_gen_ai_messages(truncated_messages, max_bytes)
    metadata = get_messages_metadata(messages, truncated_messages)

    return {
        "serialized_data": serialized_data,
        "metadata": metadata,
        "original_size": original_size,
    }

import inspect
import json
from collections import deque
from copy import deepcopy
from sys import getsizeof
from typing import TYPE_CHECKING

from sentry_sdk._types import BLOB_DATA_SUBSTITUTE

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple

    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.utils import logger

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


def get_modality_from_mime_type(mime_type: str) -> str:
    """
    Infer the content modality from a MIME type string.

    Args:
        mime_type: A MIME type string (e.g., "image/jpeg", "audio/mp3")

    Returns:
        One of: "image", "audio", "video", or "document"
        Defaults to "image" for unknown or empty MIME types.

    Examples:
        "image/jpeg" -> "image"
        "audio/mp3" -> "audio"
        "video/mp4" -> "video"
        "application/pdf" -> "document"
        "text/plain" -> "document"
    """
    if not mime_type:
        return "image"  # Default fallback

    mime_lower = mime_type.lower()
    if mime_lower.startswith("image/"):
        return "image"
    elif mime_lower.startswith("audio/"):
        return "audio"
    elif mime_lower.startswith("video/"):
        return "video"
    elif mime_lower.startswith("application/") or mime_lower.startswith("text/"):
        return "document"
    else:
        return "image"  # Default fallback for unknown types


def transform_content_part(
    content_part: "Dict[str, Any]",
) -> "Optional[Dict[str, Any]]":
    """
    Transform a content part from various AI SDK formats to Sentry's standardized format.

    Supported input formats:
    - OpenAI/LiteLLM: {"type": "image_url", "image_url": {"url": "..."}}
    - Anthropic: {"type": "image|document", "source": {"type": "base64|url|file", ...}}
    - Google: {"inline_data": {...}} or {"file_data": {...}}
    - Generic: {"type": "image|audio|video|file", "base64|url|file_id": "...", "mime_type": "..."}

    Output format (one of):
    - {"type": "blob", "modality": "...", "mime_type": "...", "content": "..."}
    - {"type": "uri", "modality": "...", "mime_type": "...", "uri": "..."}
    - {"type": "file", "modality": "...", "mime_type": "...", "file_id": "..."}

    Args:
        content_part: A dictionary representing a content part from an AI SDK

    Returns:
        A transformed dictionary in standardized format, or None if the format
        is unrecognized or transformation fails.
    """
    if not isinstance(content_part, dict):
        return None

    block_type = content_part.get("type")

    # Handle OpenAI/LiteLLM image_url format
    # {"type": "image_url", "image_url": {"url": "..."}} or {"type": "image_url", "image_url": "..."}
    if block_type == "image_url":
        image_url_data = content_part.get("image_url")
        if isinstance(image_url_data, str):
            url = image_url_data
        elif isinstance(image_url_data, dict):
            url = image_url_data.get("url", "")
        else:
            return None

        if not url:
            return None

        # Check if it's a data URI (base64 encoded)
        if url.startswith("data:"):
            try:
                mime_type, content = parse_data_uri(url)
                return {
                    "type": "blob",
                    "modality": get_modality_from_mime_type(mime_type),
                    "mime_type": mime_type,
                    "content": content,
                }
            except ValueError:
                # If parsing fails, return as URI
                return {
                    "type": "uri",
                    "modality": "image",
                    "mime_type": "",
                    "uri": url,
                }
        else:
            # Regular URL
            return {
                "type": "uri",
                "modality": "image",
                "mime_type": "",
                "uri": url,
            }

    # Handle Anthropic format with source dict
    # {"type": "image|document", "source": {"type": "base64|url|file", "media_type": "...", "data|url|file_id": "..."}}
    if block_type in ("image", "document") and "source" in content_part:
        source = content_part.get("source")
        if not isinstance(source, dict):
            return None

        source_type = source.get("type")
        media_type = source.get("media_type", "")
        modality = (
            "document"
            if block_type == "document"
            else get_modality_from_mime_type(media_type)
        )

        if source_type == "base64":
            return {
                "type": "blob",
                "modality": modality,
                "mime_type": media_type,
                "content": source.get("data", ""),
            }
        elif source_type == "url":
            return {
                "type": "uri",
                "modality": modality,
                "mime_type": media_type,
                "uri": source.get("url", ""),
            }
        elif source_type == "file":
            return {
                "type": "file",
                "modality": modality,
                "mime_type": media_type,
                "file_id": source.get("file_id", ""),
            }
        return None

    # Handle Google inline_data format
    # {"inline_data": {"mime_type": "...", "data": "..."}}
    if "inline_data" in content_part:
        inline_data = content_part.get("inline_data")
        if isinstance(inline_data, dict):
            mime_type = inline_data.get("mime_type", "")
            return {
                "type": "blob",
                "modality": get_modality_from_mime_type(mime_type),
                "mime_type": mime_type,
                "content": inline_data.get("data", ""),
            }
        return None

    # Handle Google file_data format
    # {"file_data": {"mime_type": "...", "file_uri": "..."}}
    if "file_data" in content_part:
        file_data = content_part.get("file_data")
        if isinstance(file_data, dict):
            mime_type = file_data.get("mime_type", "")
            return {
                "type": "uri",
                "modality": get_modality_from_mime_type(mime_type),
                "mime_type": mime_type,
                "uri": file_data.get("file_uri", ""),
            }
        return None

    # Handle generic format with direct fields (LangChain style)
    # {"type": "image|audio|video|file", "base64|url|file_id": "...", "mime_type": "..."}
    if block_type in ("image", "audio", "video", "file"):
        mime_type = content_part.get("mime_type", "")
        modality = block_type if block_type != "file" else "document"

        # Check for base64 encoded content
        if "base64" in content_part:
            return {
                "type": "blob",
                "modality": modality,
                "mime_type": mime_type,
                "content": content_part.get("base64", ""),
            }
        # Check for URL reference
        elif "url" in content_part:
            return {
                "type": "uri",
                "modality": modality,
                "mime_type": mime_type,
                "uri": content_part.get("url", ""),
            }
        # Check for file_id reference
        elif "file_id" in content_part:
            return {
                "type": "file",
                "modality": modality,
                "mime_type": mime_type,
                "file_id": content_part.get("file_id", ""),
            }

    # Unrecognized format
    return None


def transform_message_content(content: "Any") -> "Any":
    """
    Transform message content, handling both string content and list of content blocks.

    For list content, each item is transformed using transform_content_part().
    Items that cannot be transformed (return None) are kept as-is.

    Args:
        content: Message content - can be a string, list of content blocks, or other

    Returns:
        - String content: returned as-is
        - List content: list with each transformable item converted to standardized format
        - Other: returned as-is
    """
    if isinstance(content, str):
        return content

    if isinstance(content, (list, tuple)):
        transformed = []
        for item in content:
            if isinstance(item, dict):
                result = transform_content_part(item)
                # If transformation succeeded, use the result; otherwise keep original
                transformed.append(result if result is not None else item)
            else:
                transformed.append(item)
        return transformed

    return content


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
    span: "Span", key: str, value: "Any", unpack: bool = True
) -> None:
    normalized = _normalize_data(value, unpack=unpack)
    if isinstance(normalized, (int, float, bool, str)):
        span.set_data(key, normalized)
    else:
        span.set_data(key, json.dumps(normalized))


def extract_response_output(
    output_items: "Any",
) -> "Tuple[List[Any], List[Dict[str, Any]]]":
    """
    Extract response text and tool calls from OpenAI Responses API output.

    This handles the output format from OpenAI's Responses API where each output
    item has a `type` field that can be "message" or "function_call".

    Args:
        output_items: Iterable of output items from the response

    Returns:
        Tuple of (response_texts, tool_calls) where:
        - response_texts: List of text strings or dicts for unknown message types
        - tool_calls: List of tool call dicts
    """
    response_texts = []  # type: List[Any]
    tool_calls = []  # type: List[Dict[str, Any]]

    for output in output_items:
        if output.type == "function_call":
            if hasattr(output, "model_dump"):
                tool_calls.append(output.model_dump())
            elif hasattr(output, "dict"):
                tool_calls.append(output.dict())
        elif output.type == "message":
            for output_message in output.content:
                try:
                    response_texts.append(output_message.text)
                except AttributeError:
                    # Unknown output message type, just return the json
                    if hasattr(output_message, "model_dump"):
                        response_texts.append(output_message.model_dump())
                    elif hasattr(output_message, "dict"):
                        response_texts.append(output_message.dict())

    return response_texts, tool_calls


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
    current_span = sentry_sdk.get_current_span()
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

    if not isinstance(content, str) or len(content) <= max_chars:
        return message

    message["content"] = content[:max_chars] + "..."
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


def redact_blob_message_parts(
    messages: "List[Dict[str, Any]]",
) -> "List[Dict[str, Any]]":
    """
    Redact blob message parts from the messages by replacing blob content with "[Filtered]".

    This function creates a deep copy of messages that contain blob content to avoid
    mutating the original message dictionaries. Messages without blob content are
    returned as-is to minimize copying overhead.

    e.g:
    {
        "role": "user",
        "content": [
            {
                "text": "How many ponies do you see in the image?",
                "type": "text"
            },
            {
                "type": "blob",
                "modality": "image",
                "mime_type": "image/jpeg",
                "content": "data:image/jpeg;base64,..."
            }
        ]
    }
    becomes:
    {
        "role": "user",
        "content": [
            {
                "text": "How many ponies do you see in the image?",
                "type": "text"
            },
            {
                "type": "blob",
                "modality": "image",
                "mime_type": "image/jpeg",
                "content": "[Filtered]"
            }
        ]
    }
    """

    # First pass: check if any message contains blob content
    has_blobs = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "blob":
                    has_blobs = True
                    break
        if has_blobs:
            break

    # If no blobs found, return original messages to avoid unnecessary copying
    if not has_blobs:
        return messages

    # Deep copy messages to avoid mutating the original
    messages_copy = deepcopy(messages)

    # Second pass: redact blob content in the copy
    for message in messages_copy:
        if not isinstance(message, dict):
            continue

        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "blob":
                    item["content"] = BLOB_DATA_SUBSTITUTE

    return messages_copy


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
    max_bytes: int = MAX_GEN_AI_MESSAGE_BYTES,
) -> "Optional[List[Dict[str, Any]]]":
    if not messages:
        return None

    messages = redact_blob_message_parts(messages)

    truncated_messages, removed_count = truncate_messages_by_size(messages, max_bytes)
    if removed_count > 0:
        scope._gen_ai_original_message_count[span.span_id] = len(messages)

    return truncated_messages

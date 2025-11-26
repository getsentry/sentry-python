import inspect
import json
from collections import deque
from typing import TYPE_CHECKING
from sys import getsizeof

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Tuple

    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.utils import logger

MAX_GEN_AI_MESSAGE_BYTES = 20_000  # 20KB


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


def _normalize_data(data, unpack=True):
    # type: (Any, bool) -> Any
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


def set_data_normalized(span, key, value, unpack=True):
    # type: (Span, str, Any, bool) -> None
    normalized = _normalize_data(value, unpack=unpack)
    if isinstance(normalized, (int, float, bool, str)):
        span.set_data(key, normalized)
    else:
        span.set_data(key, json.dumps(normalized))


def normalize_message_role(role):
    # type: (str) -> str
    """
    Normalize a message role to one of the 4 allowed gen_ai role values.
    Maps "ai" -> "assistant" and keeps other standard roles unchanged.
    """
    return GEN_AI_MESSAGE_ROLE_MAPPING.get(role, role)


def normalize_message_roles(messages):
    # type: (list[dict[str, Any]]) -> list[dict[str, Any]]
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


def get_start_span_function():
    # type: () -> Callable[..., Any]
    current_span = sentry_sdk.get_current_span()
    transaction_exists = (
        current_span is not None and current_span.containing_transaction is not None
    )
    return sentry_sdk.start_span if transaction_exists else sentry_sdk.start_transaction


def _find_truncation_index(messages, max_bytes):
    # type: (List[Dict[str, Any]], int) -> int
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


def truncate_messages_by_size(messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES):
    # type: (List[Dict[str, Any]], int) -> Tuple[List[Dict[str, Any]], int]
    serialized_json = json.dumps(messages, separators=(",", ":"))
    current_size = len(serialized_json.encode("utf-8"))

    if current_size <= max_bytes:
        return messages, 0

    truncation_index = _find_truncation_index(messages, max_bytes)
    return messages[truncation_index:], truncation_index


def truncate_and_annotate_messages(
    messages, span, scope, max_bytes=MAX_GEN_AI_MESSAGE_BYTES
):
    # type: (Optional[List[Dict[str, Any]]], Any, Any, int) -> Optional[List[Dict[str, Any]]]
    if not messages:
        return None

    truncated_messages, removed_count = truncate_messages_by_size(messages, max_bytes)
    if removed_count > 0:
        scope._gen_ai_original_message_count[span.span_id] = len(messages)

    return truncated_messages

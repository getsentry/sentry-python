"""Utility functions for PydanticAI span instrumentation."""

import sentry_sdk
from sentry_sdk._types import BLOB_DATA_SUBSTITUTE
from sentry_sdk.ai.utils import get_modality_from_mime_type
from sentry_sdk.consts import SPANDATA

from ..consts import DATA_URL_BASE64_REGEX
try:
    from pydantic_ai.messages import (
        BaseToolCallPart,
        BaseToolReturnPart,
        SystemPromptPart,
        TextPart,
        ThinkingPart,
        BinaryContent,
        ImageUrl,
    )
except ImportError:
    BaseToolCallPart = None
    BaseToolReturnPart = None
    SystemPromptPart = None
    TextPart = None
    ThinkingPart = None
    BinaryContent = None
    ImageUrl = None


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union, Dict, Any
    from pydantic_ai.usage import RequestUsage, RunUsage  # type: ignore


def _serialize_image_url_item(item: "Any") -> "Dict[str, Any]":
    """Serialize an ImageUrl content item for span data.

    For data URLs containing base64-encoded images, the content is redacted.
    For regular HTTP URLs, the URL string is preserved.
    """
    url = str(item.url)
    data_url_match = DATA_URL_BASE64_REGEX.match(url)

    if data_url_match:
        return {
            "type": "image",
            "content": BLOB_DATA_SUBSTITUTE,
        }

    return {
        "type": "image",
        "content": url,
    }


def _serialize_binary_content_item(item: "Any") -> "Dict[str, Any]":
    """Serialize a BinaryContent item for span data, redacting the blob data."""
    return {
        "type": "blob",
        "modality": get_modality_from_mime_type(item.media_type),
        "mime_type": item.media_type,
        "content": BLOB_DATA_SUBSTITUTE,
    }


def _set_usage_data(
    span: "sentry_sdk.tracing.Span", usage: "Union[RequestUsage, RunUsage]"
) -> None:
    """Set token usage data on a span.

    This function works with both RequestUsage (single request) and
    RunUsage (agent run) objects from pydantic_ai.

    Args:
        span: The Sentry span to set data on.
        usage: RequestUsage or RunUsage object containing token usage information.
    """
    if usage is None:
        return

    if hasattr(usage, "input_tokens") and usage.input_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens)

    # Pydantic AI uses cache_read_tokens (not input_tokens_cached)
    if hasattr(usage, "cache_read_tokens") and usage.cache_read_tokens is not None:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, usage.cache_read_tokens
        )

    # Pydantic AI uses cache_write_tokens (not input_tokens_cache_write)
    if hasattr(usage, "cache_write_tokens") and usage.cache_write_tokens is not None:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE,
            usage.cache_write_tokens,
        )

    if hasattr(usage, "output_tokens") and usage.output_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens)

    if hasattr(usage, "total_tokens") and usage.total_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_tokens)


def _format_messages(messages):
    """Format PydanticAI messages into Sentry's standardized format."""
    formatted_messages = []
    from sentry_sdk.utils import safe_serialize

    if not messages:
        return formatted_messages

    try:
        # Handle single message if passed as non-list
        messages_list = messages if isinstance(messages, list) else [messages]
        
        for msg in messages_list:
            if hasattr(msg, "parts"):
                for part in msg.parts:
                    role = "user"
                    if SystemPromptPart and isinstance(part, SystemPromptPart):
                        continue
                    elif (
                        (TextPart and isinstance(part, TextPart))
                        or (ThinkingPart and isinstance(part, ThinkingPart))
                        or (BaseToolCallPart and isinstance(part, BaseToolCallPart))
                    ):
                        role = "assistant"
                    elif BaseToolReturnPart and isinstance(part, BaseToolReturnPart):
                        role = "tool"

                    content = []
                    tool_calls = None
                    tool_call_id = None

                    if BaseToolCallPart and isinstance(part, BaseToolCallPart):
                        tool_call_data = {}
                        if hasattr(part, "tool_name"):
                            tool_call_data["name"] = part.tool_name
                        if hasattr(part, "args"):
                            tool_call_data["arguments"] = safe_serialize(part.args)
                        if tool_call_data:
                            tool_calls = [tool_call_data]
                    elif BaseToolReturnPart and isinstance(part, BaseToolReturnPart):
                        if hasattr(part, "tool_name"):
                            tool_call_id = part.tool_name
                        if hasattr(part, "content"):
                            content.append({"type": "text", "text": str(part.content)})
                    elif hasattr(part, "content"):
                        if isinstance(part.content, str):
                            content.append({"type": "text", "text": part.content})
                        elif isinstance(part.content, list):
                            for item in part.content:
                                if isinstance(item, str):
                                    content.append({"type": "text", "text": item})
                                elif ImageUrl and isinstance(item, ImageUrl):
                                    content.append(_serialize_image_url_item(item))
                                elif BinaryContent and isinstance(item, BinaryContent):
                                    content.append(_serialize_binary_content_item(item))
                                else:
                                    content.append(safe_serialize(item))
                        else:
                            content.append({"type": "text", "text": str(part.content)})

                    if content or tool_calls:
                        message = {"role": role}
                        if content:
                            message["content"] = content
                        if tool_calls:
                            message["tool_calls"] = tool_calls
                        if tool_call_id:
                            message["tool_call_id"] = tool_call_id
                        formatted_messages.append(message)
    except Exception:
        pass

    return formatted_messages

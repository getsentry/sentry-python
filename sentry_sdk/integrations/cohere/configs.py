from sentry_sdk.ai.utils import (
    get_first_from_sources,
    transform_message_content,
)
from sentry_sdk.consts import SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from sentry_sdk.ai.span_config import OperationConfig


# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize_embedding_input(texts):
    # type: (Any) -> Any
    if isinstance(texts, list):
        return texts
    if isinstance(texts, tuple):
        return list(texts)
    return [texts]


def _extract_v1_messages(kwargs):
    # type: (dict[str, Any]) -> list[dict[str, str]]
    messages = []
    for x in kwargs.get("chat_history", []):
        messages.append(
            {
                "role": getattr(x, "role", ""),
                "content": transform_message_content(getattr(x, "message", "")),
            }
        )
    message = kwargs.get("message")
    if message:
        messages.append({"role": "user", "content": transform_message_content(message)})
    return messages


def _extract_v1_response_text(response):
    # type: (Any) -> list[str] | None
    text = getattr(response, "text", None)
    return [text] if text is not None else None


def _extract_v2_messages(messages):
    # type: (Any) -> list[dict[str, Any]]
    result = []
    for msg in messages:
        role = msg["role"] if isinstance(msg, dict) else getattr(msg, "role", "unknown")
        content = (
            msg["content"] if isinstance(msg, dict) else getattr(msg, "content", "")
        )
        result.append({"role": role, "content": transform_message_content(content)})
    return result


def _extract_v2_response_text(response):
    # type: (Any) -> list[str] | None
    content = get_first_from_sources(response, [("message", "content")], True)
    if content:
        texts = [item.text for item in content if hasattr(item, "text")]
        if texts:
            return texts
    return None


# ── Configs ──────────────────────────────────────────────────────────────────


COHERE_EMBED_CONFIG: "OperationConfig" = {
    "static": {
        SPANDATA.GEN_AI_SYSTEM: "cohere",
        SPANDATA.GEN_AI_OPERATION_NAME: "embeddings",
    },
    "params": {"model": SPANDATA.GEN_AI_REQUEST_MODEL},
    "extract_messages": lambda kw: (
        _normalize_embedding_input(kw["texts"]) if "texts" in kw else None
    ),
    "message_target": SPANDATA.GEN_AI_EMBEDDINGS_INPUT,
    "response": {
        "usage": {
            "input_tokens": [("meta", "billed_units", "input_tokens")],
            "total_tokens": [("meta", "billed_units", "input_tokens")],
        },
    },
}


COHERE_V1_CHAT_CONFIG: "OperationConfig" = {
    "static": {
        SPANDATA.GEN_AI_SYSTEM: "cohere",
        SPANDATA.GEN_AI_OPERATION_NAME: "chat",
    },
    "extract_messages": lambda kw: _extract_v1_messages(kw),
    "response": {
        "sources": {
            SPANDATA.GEN_AI_RESPONSE_MODEL: [("model",)],
            SPANDATA.GEN_AI_RESPONSE_ID: [("generation_id",)],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("finish_reason",)],
        },
        "pii_sources": {
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS: [("tool_calls",)],
        },
        "extract_text": _extract_v1_response_text,
        "usage": {
            "input_tokens": [
                ("meta", "billed_units", "input_tokens"),
                ("meta", "tokens", "input_tokens"),
            ],
            "output_tokens": [
                ("meta", "billed_units", "output_tokens"),
                ("meta", "tokens", "output_tokens"),
            ],
        },
    },
    "stream_response_object": [("response",)],
}


STREAM_DELTA_TEXT_SOURCES = [("delta", "message", "content", "text")]


COHERE_V2_CHAT_CONFIG: "OperationConfig" = {
    "static": {
        SPANDATA.GEN_AI_SYSTEM: "cohere",
        SPANDATA.GEN_AI_OPERATION_NAME: "chat",
    },
    "pii_params": {
        "tools": SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
    },
    "extract_messages": lambda kw: _extract_v2_messages(kw.get("messages", [])),
    "response": {
        "sources": {
            SPANDATA.GEN_AI_RESPONSE_MODEL: [("model",)],
            SPANDATA.GEN_AI_RESPONSE_ID: [("id",)],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("finish_reason",)],
        },
        "pii_sources": {
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS: [("message", "tool_calls")],
        },
        "extract_text": _extract_v2_response_text,
        "usage": {
            "input_tokens": [
                ("usage", "billed_units", "input_tokens"),
                ("usage", "tokens", "input_tokens"),
            ],
            "output_tokens": [
                ("usage", "billed_units", "output_tokens"),
                ("usage", "tokens", "output_tokens"),
            ],
        },
    },
    "stream_response": {
        "sources": {
            SPANDATA.GEN_AI_RESPONSE_ID: [("id",)],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("delta", "finish_reason")],
        },
        "usage": {
            "input_tokens": [
                ("delta", "usage", "billed_units", "input_tokens"),
                ("delta", "usage", "tokens", "input_tokens"),
            ],
            "output_tokens": [
                ("delta", "usage", "billed_units", "output_tokens"),
                ("delta", "usage", "tokens", "output_tokens"),
            ],
        },
    },
}

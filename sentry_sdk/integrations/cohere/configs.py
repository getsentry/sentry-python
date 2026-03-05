from sentry_sdk.consts import SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Sequence, Tuple
    from typing_extensions import TypedDict

    # Source paths: list of attribute chains to try in order.
    # e.g. [("meta", "billed_units", "input_tokens"), ("meta", "tokens", "input_tokens")]
    SourcePaths = Sequence[Tuple[str, ...]]

    # Maps a SPANDATA key to source paths on the response object.
    # e.g. {SPANDATA.GEN_AI_RESPONSE_ID: [("id",)]}
    SourceMapping = Dict[str, SourcePaths]

    class UsageConfig(TypedDict, total=False):
        """Declarative token usage extraction paths (from response object)."""

        input_tokens: SourcePaths
        output_tokens: SourcePaths
        total_tokens: SourcePaths

    class ResponseConfig(TypedDict, total=False):
        """Declarative response span data config."""

        # Attributes always extracted from the response object.
        sources: SourceMapping
        # Attributes extracted only when PII sending is enabled.
        pii_sources: SourceMapping
        # Declarative token usage paths.
        usage: UsageConfig

    class OperationConfig(TypedDict, total=False):
        """Full declarative config for an AI operation (chat, embeddings, etc.)."""

        # Key/value pairs set on every span unconditionally.
        static: Dict[str, Any]
        # Maps kwarg names to SPANDATA keys (always set if present in kwargs).
        params: Dict[str, str]
        # Maps kwarg names to SPANDATA keys (only set when PII is enabled).
        pii_params: Dict[str, str]
        # Non-streaming response config.
        response: ResponseConfig
        # Streaming response config (different attribute paths).
        stream_response: ResponseConfig
        # Source paths to extract a full response object from a stream-end event
        # (V1 pattern: reuse "response" config after extracting).
        stream_response_object: SourcePaths


# ── Configs ──────────────────────────────────────────────────────────────────


COHERE_EMBED_CONFIG: "OperationConfig" = {
    "static": {
        SPANDATA.GEN_AI_SYSTEM: "cohere",
        SPANDATA.GEN_AI_OPERATION_NAME: "embeddings",
    },
    "params": {"model": SPANDATA.GEN_AI_REQUEST_MODEL},
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
    "response": {
        "sources": {
            SPANDATA.GEN_AI_RESPONSE_MODEL: [("model",)],
            SPANDATA.GEN_AI_RESPONSE_ID: [("generation_id",)],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("finish_reason",)],
        },
        "pii_sources": {
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS: [("tool_calls",)],
        },
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
    "response": {
        "sources": {
            SPANDATA.GEN_AI_RESPONSE_MODEL: [("model",)],
            SPANDATA.GEN_AI_RESPONSE_ID: [("id",)],
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("finish_reason",)],
        },
        "pii_sources": {
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS: [("message", "tool_calls")],
        },
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

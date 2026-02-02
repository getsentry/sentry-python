from typing import TYPE_CHECKING, Any, List, TypedDict, Optional, Union

from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    safe_serialize,
)
from .utils import (
    extract_tool_calls,
    extract_finish_reasons,
    extract_contents_text,
    extract_usage_data,
    UsageData,
)

if TYPE_CHECKING:
    from sentry_sdk.tracing import Span
    from google.genai.types import GenerateContentResponse


class AccumulatedResponse(TypedDict):
    id: "Optional[str]"
    model: "Optional[str]"
    text: str
    finish_reasons: "List[str]"
    tool_calls: "List[dict[str, Any]]"
    usage_metadata: "Optional[UsageData]"


def element_wise_usage_max(self: "UsageData", other: "UsageData") -> "UsageData":
    return UsageData(
        input_tokens=max(self["input_tokens"], other["input_tokens"]),
        output_tokens=max(self["output_tokens"], other["output_tokens"]),
        input_tokens_cached=max(
            self["input_tokens_cached"], other["input_tokens_cached"]
        ),
        output_tokens_reasoning=max(
            self["output_tokens_reasoning"], other["output_tokens_reasoning"]
        ),
        total_tokens=max(self["total_tokens"], other["total_tokens"]),
    )


def accumulate_streaming_response(
    chunks: "List[GenerateContentResponse]",
) -> "AccumulatedResponse":
    """Accumulate streaming chunks into a single response-like object."""
    accumulated_text = []
    finish_reasons = []
    tool_calls = []
    usage_data = None
    response_id = None
    model = None

    for chunk in chunks:
        # Extract text and tool calls
        if getattr(chunk, "candidates", None):
            for candidate in getattr(chunk, "candidates", []):
                if hasattr(candidate, "content") and getattr(
                    candidate.content, "parts", []
                ):
                    extracted_text = extract_contents_text(candidate.content)
                    if extracted_text:
                        accumulated_text.append(extracted_text)

        extracted_finish_reasons = extract_finish_reasons(chunk)
        if extracted_finish_reasons:
            finish_reasons.extend(extracted_finish_reasons)

        extracted_tool_calls = extract_tool_calls(chunk)
        if extracted_tool_calls:
            tool_calls.extend(extracted_tool_calls)

        # Use last possible chunk, in case of interruption, and
        # gracefully handle missing intermediate tokens by taking maximum
        # with previous token reporting.
        chunk_usage_data = extract_usage_data(chunk)
        usage_data = (
            chunk_usage_data
            if usage_data is None
            else element_wise_usage_max(usage_data, chunk_usage_data)
        )

    accumulated_response = AccumulatedResponse(
        text="".join(accumulated_text),
        finish_reasons=finish_reasons,
        tool_calls=tool_calls,
        usage_metadata=usage_data,
        id=response_id,
        model=model,
    )

    return accumulated_response


def set_span_data_for_streaming_response(
    span: "Span", integration: "Any", accumulated_response: "AccumulatedResponse"
) -> None:
    """Set span data for accumulated streaming response."""
    if (
        should_send_default_pii()
        and integration.include_prompts
        and accumulated_response.get("text")
    ):
        span.set_data(
            SPANDATA.GEN_AI_RESPONSE_TEXT,
            safe_serialize([accumulated_response["text"]]),
        )

    if accumulated_response.get("finish_reasons"):
        set_data_normalized(
            span,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS,
            accumulated_response["finish_reasons"],
        )

    if accumulated_response.get("tool_calls"):
        span.set_data(
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
            safe_serialize(accumulated_response["tool_calls"]),
        )

    if accumulated_response.get("id"):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_ID, accumulated_response["id"])
    if accumulated_response.get("model"):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, accumulated_response["model"])

    if accumulated_response["usage_metadata"] is None:
        return

    if accumulated_response["usage_metadata"]["input_tokens"]:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS,
            accumulated_response["usage_metadata"]["input_tokens"],
        )

    if accumulated_response["usage_metadata"]["input_tokens_cached"]:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
            accumulated_response["usage_metadata"]["input_tokens_cached"],
        )

    if accumulated_response["usage_metadata"]["output_tokens"]:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS,
            accumulated_response["usage_metadata"]["output_tokens"],
        )

    if accumulated_response["usage_metadata"]["output_tokens_reasoning"]:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
            accumulated_response["usage_metadata"]["output_tokens_reasoning"],
        )

    if accumulated_response["usage_metadata"]["total_tokens"]:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS,
            accumulated_response["usage_metadata"]["total_tokens"],
        )

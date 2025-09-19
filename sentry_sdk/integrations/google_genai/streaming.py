from typing import (
    TYPE_CHECKING,
    Any,
    List,
)

from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    safe_serialize,
)
from .utils import (
    get_model_name,
    wrapped_config_with_tools,
)

if TYPE_CHECKING:
    from sentry_sdk.tracing import Span


def prepare_generate_content_args(args, kwargs):
    # type: (tuple, dict[str, Any]) -> tuple[Any, Any, str]
    """Extract and prepare common arguments for generate_content methods."""
    model = args[0] if args else kwargs.get("model", "unknown")
    contents = args[1] if len(args) > 1 else kwargs.get("contents")
    model_name = get_model_name(model)

    # Wrap config with tools
    config = kwargs.get("config")
    wrapped_config = wrapped_config_with_tools(config)
    if wrapped_config is not config:
        kwargs["config"] = wrapped_config

    return model, contents, model_name


def accumulate_streaming_response(chunks):
    # type: (List[Any]) -> dict[str, Any]
    """Accumulate streaming chunks into a single response-like object."""
    accumulated_text = []
    finish_reasons = []
    tool_calls = []
    total_prompt_tokens = 0
    total_tool_use_prompt_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_cached_tokens = 0
    total_reasoning_tokens = 0
    response_id = None
    model = None

    for chunk in chunks:
        # Extract text and tool calls
        if hasattr(chunk, "candidates") and chunk.candidates:
            for candidate in chunk.candidates:
                if hasattr(candidate, "content") and hasattr(
                    candidate.content, "parts"
                ):
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            accumulated_text.append(part.text)

                        # Extract function calls
                        if hasattr(part, "function_call") and part.function_call:
                            function_call = part.function_call
                            tool_call = {
                                "name": getattr(function_call, "name", None),
                                "type": "function_call",
                            }
                            if hasattr(function_call, "args"):
                                tool_call["arguments"] = safe_serialize(
                                    function_call.args
                                )
                            tool_calls.append(tool_call)

                # Get finish reason from last chunk
                if hasattr(candidate, "finish_reason") and candidate.finish_reason:
                    reason = str(candidate.finish_reason)
                    if "." in reason:
                        reason = reason.split(".")[-1]
                    if reason not in finish_reasons:
                        finish_reasons.append(reason)

        # Extract from automatic_function_calling_history
        if (
            hasattr(chunk, "automatic_function_calling_history")
            and chunk.automatic_function_calling_history
        ):
            for content in chunk.automatic_function_calling_history:
                if hasattr(content, "parts") and content.parts:
                    for part in content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            function_call = part.function_call
                            tool_call = {
                                "name": getattr(function_call, "name", None),
                                "type": "function_call",
                            }
                            if hasattr(function_call, "args"):
                                tool_call["arguments"] = safe_serialize(
                                    function_call.args
                                )
                            tool_calls.append(tool_call)

        # Accumulate token usage
        if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
            usage = chunk.usage_metadata
            if (
                hasattr(usage, "prompt_token_count")
                and usage.prompt_token_count is not None
            ):
                total_prompt_tokens = max(total_prompt_tokens, usage.prompt_token_count)
            if (
                hasattr(usage, "tool_use_prompt_token_count")
                and usage.tool_use_prompt_token_count is not None
            ):
                total_tool_use_prompt_tokens = max(
                    total_tool_use_prompt_tokens, usage.tool_use_prompt_token_count
                )
            if (
                hasattr(usage, "candidates_token_count")
                and usage.candidates_token_count is not None
            ):
                total_output_tokens += usage.candidates_token_count
            if (
                hasattr(usage, "cached_content_token_count")
                and usage.cached_content_token_count is not None
            ):
                total_cached_tokens = max(
                    total_cached_tokens, usage.cached_content_token_count
                )
            if (
                hasattr(usage, "thoughts_token_count")
                and usage.thoughts_token_count is not None
            ):
                total_reasoning_tokens += usage.thoughts_token_count
            if (
                hasattr(usage, "total_token_count")
                and usage.total_token_count is not None
            ):
                # Only use the final total_token_count from the last chunk
                total_tokens = usage.total_token_count

        # Get response metadata from first chunk with it
        if response_id is None and hasattr(chunk, "response_id") and chunk.response_id:
            response_id = chunk.response_id
        if model is None and hasattr(chunk, "model_version") and chunk.model_version:
            model = chunk.model_version

    # Create a synthetic response object with accumulated data
    accumulated_response = {
        "text": "".join(accumulated_text),
        "finish_reasons": finish_reasons,
        "tool_calls": tool_calls,
        "usage_metadata": {
            "prompt_token_count": total_prompt_tokens,
            "candidates_token_count": total_output_tokens,  # Keep original output tokens
            "cached_content_token_count": total_cached_tokens,
            "thoughts_token_count": total_reasoning_tokens,
            "total_token_count": (
                total_tokens
                if total_tokens > 0
                else (
                    total_prompt_tokens
                    + total_tool_use_prompt_tokens
                    + total_output_tokens
                    + total_reasoning_tokens
                    + total_cached_tokens
                )
            ),
        },
    }

    # Add optional token counts if present
    if total_tool_use_prompt_tokens > 0:
        accumulated_response["usage_metadata"]["tool_use_prompt_token_count"] = (
            total_tool_use_prompt_tokens
        )
    if total_cached_tokens > 0:
        accumulated_response["usage_metadata"]["cached_content_token_count"] = (
            total_cached_tokens
        )
    if total_reasoning_tokens > 0:
        accumulated_response["usage_metadata"]["thoughts_token_count"] = (
            total_reasoning_tokens
        )

    if response_id:
        accumulated_response["id"] = response_id
    if model:
        accumulated_response["model"] = model

    return accumulated_response


def set_span_data_for_streaming_response(span, integration, accumulated_response):
    # type: (Span, Any, dict[str, Any]) -> None
    """Set span data for accumulated streaming response."""
    # Set response text
    if (
        should_send_default_pii()
        and integration.include_prompts
        and accumulated_response.get("text")
    ):
        span.set_data(
            SPANDATA.GEN_AI_RESPONSE_TEXT,
            safe_serialize([accumulated_response["text"]]),
        )

    # Set finish reasons
    if accumulated_response.get("finish_reasons"):
        set_data_normalized(
            span,
            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS,
            accumulated_response["finish_reasons"],
        )

    # Set tool calls
    if accumulated_response.get("tool_calls"):
        span.set_data(
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
            safe_serialize(accumulated_response["tool_calls"]),
        )

    # Set response ID and model
    if accumulated_response.get("id"):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_ID, accumulated_response["id"])
    if accumulated_response.get("model"):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, accumulated_response["model"])

    # Set token usage
    usage = accumulated_response.get("usage_metadata", {})

    # Input tokens should include both prompt and tool use prompt tokens
    prompt_tokens = usage.get("prompt_token_count", 0)
    tool_use_prompt_tokens = usage.get("tool_use_prompt_token_count", 0)
    if prompt_tokens or tool_use_prompt_tokens:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, prompt_tokens + tool_use_prompt_tokens
        )

    # Output tokens should include reasoning tokens
    output_tokens = usage.get("candidates_token_count", 0)
    reasoning_tokens = usage.get("thoughts_token_count", 0)
    if output_tokens or reasoning_tokens:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens + reasoning_tokens
        )

    if usage.get("total_token_count"):
        span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["total_token_count"])
    if usage.get("cached_content_token_count"):
        span.set_data(
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
            usage["cached_content_token_count"],
        )
    if usage.get("thoughts_token_count"):
        span.set_data(
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
            usage["thoughts_token_count"],
        )

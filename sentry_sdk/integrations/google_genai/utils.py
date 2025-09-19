import copy
import inspect
from functools import wraps
from .consts import ORIGIN, TOOL_ATTRIBUTES_MAP, GEN_AI_SYSTEM
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    List,
    Optional,
    Union,
)

import sentry_sdk
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    safe_serialize,
)

if TYPE_CHECKING:
    from sentry_sdk.tracing import Span
    from google.genai.types import (
        GenerateContentResponse,
        ContentListUnion,
        GenerateContentConfig,
        Tool,
        Model,
    )


def capture_exception(exc):
    # type: (Any) -> None
    """Capture exception with Google GenAI mechanism."""
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "google_genai", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def get_model_name(model):
    # type: (Union[str, Model]) -> str
    """Extract model name from model parameter."""
    if isinstance(model, str):
        return model
    # Handle case where model might be an object with a name attribute
    if hasattr(model, "name"):
        return str(model.name)
    return str(model)


def _extract_contents_text(contents):
    # type: (ContentListUnion) -> Optional[str]
    """Extract text from contents parameter which can have various formats."""
    if contents is None:
        return None

    # Simple string case
    if isinstance(contents, str):
        return contents

    # List of contents or parts
    if isinstance(contents, list):
        texts = []
        for item in contents:
            # Recursively extract text from each item
            extracted = _extract_contents_text(item)
            if extracted:
                texts.append(extracted)
        return " ".join(texts) if texts else None

    # Dictionary case
    if isinstance(contents, dict):
        if "text" in contents:
            return contents["text"]
        # Try to extract from parts if present in dict
        if "parts" in contents:
            return _extract_contents_text(contents["parts"])

    # Content object with parts - recurse into parts
    if hasattr(contents, "parts") and contents.parts:
        return _extract_contents_text(contents.parts)

    # Direct text attribute
    if hasattr(contents, "text"):
        return contents.text

    return None


def _format_tools_for_span(tools):
    # type: (Tool | Callable[..., Any]) -> Optional[List[dict[str, Any]]]
    """Format tools parameter for span data."""
    formatted_tools = []
    for tool in tools:
        if callable(tool):
            # Handle callable functions passed directly
            formatted_tools.append(
                {
                    "name": getattr(tool, "__name__", "unknown"),
                    "description": getattr(tool, "__doc__", None),
                }
            )
        elif (
            hasattr(tool, "function_declarations")
            and tool.function_declarations is not None
        ):
            # Tool object with function declarations
            for func_decl in tool.function_declarations:
                formatted_tools.append(
                    {
                        "name": getattr(func_decl, "name", None),
                        "description": getattr(func_decl, "description", None),
                    }
                )
        else:
            # Check for predefined tool attributes - each of these tools
            # is an attribute of the tool object, by default set to None
            for attr_name, description in TOOL_ATTRIBUTES_MAP.items():
                if hasattr(tool, attr_name) and getattr(tool, attr_name) is not None:
                    formatted_tools.append(
                        {
                            "name": attr_name,
                            "description": description,
                        }
                    )
                    break

    return formatted_tools if formatted_tools else None


def _extract_tool_calls(response):
    # type: (GenerateContentResponse) -> Optional[List[dict[str, Any]]]
    """Extract tool/function calls from response candidates and automatic function calling history."""

    tool_calls = []

    # Extract from candidates, sometimes tool calls are nested under the content.parts object
    if hasattr(response, "candidates"):
        for candidate in response.candidates:
            if not hasattr(candidate, "content") or not hasattr(
                candidate.content, "parts"
            ):
                continue

            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    function_call = part.function_call
                    tool_call = {
                        "name": getattr(function_call, "name", None),
                        "type": "function_call",
                    }

                    # Extract arguments if available
                    if hasattr(function_call, "args"):
                        tool_call["arguments"] = safe_serialize(function_call.args)

                    tool_calls.append(tool_call)

    # Extract from automatic_function_calling_history
    # This is the history of tool calls made by the model
    if (
        hasattr(response, "automatic_function_calling_history")
        and response.automatic_function_calling_history
    ):
        for content in response.automatic_function_calling_history:
            if not hasattr(content, "parts") or not content.parts:
                continue

            for part in content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    function_call = part.function_call
                    tool_call = {
                        "name": getattr(function_call, "name", None),
                        "type": "function_call",
                    }

                    # Extract arguments if available
                    if hasattr(function_call, "args"):
                        tool_call["arguments"] = safe_serialize(function_call.args)

                    tool_calls.append(tool_call)

    return tool_calls if tool_calls else None


def _capture_tool_input(args, kwargs, tool):
    # type: (tuple, dict[str, Any], Tool) -> dict[str, Any]
    """Capture tool input from args and kwargs."""
    tool_input = kwargs.copy() if kwargs else {}

    # If we have positional args, try to map them to the function signature
    if args:
        try:
            sig = inspect.signature(tool)
            param_names = list(sig.parameters.keys())
            for i, arg in enumerate(args):
                if i < len(param_names):
                    tool_input[param_names[i]] = arg
        except Exception:
            # Fallback if we can't get the signature
            tool_input["args"] = args

    return tool_input


def _create_tool_span(tool_name, tool_doc):
    # type: (str, Optional[str]) -> Span
    """Create a span for tool execution."""
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_EXECUTE_TOOL,
        name=f"execute_tool {tool_name}",
        origin=ORIGIN,
    )
    span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool_name)
    span.set_data(SPANDATA.GEN_AI_TOOL_TYPE, "function")
    if tool_doc:
        span.set_data(SPANDATA.GEN_AI_TOOL_DESCRIPTION, tool_doc)
    return span


def wrapped_tool(tool):
    # type: (Tool | Callable[..., Any]) -> Tool | Callable[..., Any]
    """Wrap a tool to emit execute_tool spans when called."""
    if not callable(tool):
        # Not a callable function, return as-is (predefined tools)
        return tool

    tool_name = getattr(tool, "__name__", "unknown")
    tool_doc = tool.__doc__

    if inspect.iscoroutinefunction(tool):
        # Async function
        @wraps(tool)
        async def async_wrapped(*args, **kwargs):
            with _create_tool_span(tool_name, tool_doc) as span:
                # Capture tool input
                tool_input = _capture_tool_input(args, kwargs, tool)
                with capture_internal_exceptions():
                    span.set_data(
                        SPANDATA.GEN_AI_TOOL_INPUT, safe_serialize(tool_input)
                    )

                try:
                    result = await tool(*args, **kwargs)

                    # Capture tool output
                    with capture_internal_exceptions():
                        span.set_data(
                            SPANDATA.GEN_AI_TOOL_OUTPUT, safe_serialize(result)
                        )

                    return result
                except Exception as exc:
                    capture_exception(exc)
                    raise

        return async_wrapped
    else:
        # Sync function
        @wraps(tool)
        def sync_wrapped(*args, **kwargs):
            with _create_tool_span(tool_name, tool_doc) as span:
                # Capture tool input
                tool_input = _capture_tool_input(args, kwargs, tool)
                with capture_internal_exceptions():
                    span.set_data(
                        SPANDATA.GEN_AI_TOOL_INPUT, safe_serialize(tool_input)
                    )

                try:
                    result = tool(*args, **kwargs)

                    # Capture tool output
                    with capture_internal_exceptions():
                        span.set_data(
                            SPANDATA.GEN_AI_TOOL_OUTPUT, safe_serialize(result)
                        )

                    return result
                except Exception as exc:
                    capture_exception(exc)
                    raise

        return sync_wrapped


def wrapped_config_with_tools(config):
    # type: (GenerateContentConfig) -> GenerateContentConfig
    """Wrap tools in config to emit execute_tool spans. Tools are sometimes passed directly as
    callable functions as a part of the config object."""

    if not config or not hasattr(config, "tools") or not config.tools:
        return config

    result = copy.copy(config)
    result.tools = [wrapped_tool(tool) for tool in config.tools]

    return result


def _extract_response_text(response):
    # type: (GenerateContentResponse) -> Optional[List[str]]
    """Extract text from response candidates."""

    if not response or not hasattr(response, "candidates"):
        return None

    texts = []
    for candidate in response.candidates:
        if not hasattr(candidate, "content") or not hasattr(candidate.content, "parts"):
            continue

        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                texts.append(part.text)

    return texts if texts else None


def _extract_finish_reasons(response):
    # type: (GenerateContentResponse) -> Optional[List[str]]
    """Extract finish reasons from response candidates."""
    if not response or not hasattr(response, "candidates"):
        return None

    finish_reasons = []
    for candidate in response.candidates:
        if hasattr(candidate, "finish_reason") and candidate.finish_reason:
            # Convert enum value to string if necessary
            reason = str(candidate.finish_reason)
            # Remove enum prefix if present (e.g., "FinishReason.STOP" -> "STOP")
            if "." in reason:
                reason = reason.split(".")[-1]
            finish_reasons.append(reason)

    return finish_reasons if finish_reasons else None


def set_span_data_for_request(span, integration, model, contents, kwargs):
    # type: (Span, Integration, str, ContentListUnion, dict[str, Any]) -> None
    """Set span data for the request."""
    span.set_data(SPANDATA.GEN_AI_SYSTEM, GEN_AI_SYSTEM)
    span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)

    # Set model configuration parameters
    config = kwargs.get("config")

    # Extract parameters directly from config (not nested under generation_config)
    for param, span_key in [
        ("temperature", SPANDATA.GEN_AI_REQUEST_TEMPERATURE),
        ("top_p", SPANDATA.GEN_AI_REQUEST_TOP_P),
        ("top_k", SPANDATA.GEN_AI_REQUEST_TOP_K),
        ("max_output_tokens", SPANDATA.GEN_AI_REQUEST_MAX_TOKENS),
        ("presence_penalty", SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY),
        ("frequency_penalty", SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY),
        ("seed", SPANDATA.GEN_AI_REQUEST_SEED),
    ]:
        if hasattr(config, param):
            value = getattr(config, param)
            if value is not None:
                span.set_data(span_key, value)

    # Set streaming flag
    if kwargs.get("stream", False):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, True)

    # Set tools if available
    if hasattr(config, "tools"):
        tools = config.tools
        if tools:
            formatted_tools = _format_tools_for_span(tools)
            if formatted_tools:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
                    formatted_tools,
                    unpack=False,
                )

    # Set input messages/prompts if PII is allowed
    if should_send_default_pii() and integration.include_prompts:
        messages = []

        # Add system instruction if present
        if hasattr(config, "system_instruction"):
            system_instruction = config.system_instruction
            if system_instruction:
                system_text = _extract_contents_text(system_instruction)
                if system_text:
                    messages.append({"role": "system", "content": system_text})

        # Add user message
        contents_text = _extract_contents_text(contents)
        if contents_text:
            messages.append({"role": "user", "content": contents_text})

        if messages:
            set_data_normalized(
                span,
                SPANDATA.GEN_AI_REQUEST_MESSAGES,
                messages,
                unpack=False,
            )


def set_span_data_for_response(span, integration, response):
    # type: (Span, Integration, GenerateContentResponse) -> None
    """Set span data for the response."""
    if not response:
        return

    # Extract and set response text
    if should_send_default_pii() and integration.include_prompts:
        response_texts = _extract_response_text(response)
        if response_texts:
            # Format as JSON string array as per documentation
            span.set_data(SPANDATA.GEN_AI_RESPONSE_TEXT, safe_serialize(response_texts))

    # Extract and set tool calls
    tool_calls = _extract_tool_calls(response)
    if tool_calls:
        # Tool calls should be JSON serialized
        span.set_data(SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, safe_serialize(tool_calls))

    # Extract and set finish reasons
    finish_reasons = _extract_finish_reasons(response)
    if finish_reasons:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reasons
        )

    # Set response ID if available
    if hasattr(response, "response_id") and response.response_id:
        span.set_data(SPANDATA.GEN_AI_RESPONSE_ID, response.response_id)

    # Set response model if available
    if hasattr(response, "model_version") and response.model_version:
        span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, response.model_version)

    # Set token usage if available
    if hasattr(response, "usage_metadata"):
        usage = response.usage_metadata

        # Input tokens should include both prompt and tool use prompt tokens
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        tool_use_prompt_tokens = getattr(usage, "tool_use_prompt_token_count", 0) or 0
        if prompt_tokens or tool_use_prompt_tokens:
            span.set_data(
                SPANDATA.GEN_AI_USAGE_INPUT_TOKENS,
                prompt_tokens + tool_use_prompt_tokens,
            )

        # Output tokens should include reasoning tokens
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        reasoning_tokens = getattr(usage, "thoughts_token_count", 0) or 0
        if output_tokens or reasoning_tokens:
            span.set_data(
                SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens + reasoning_tokens
            )

        if hasattr(usage, "total_token_count"):
            span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_token_count)
        if hasattr(usage, "cached_content_token_count"):
            span.set_data(
                SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
                usage.cached_content_token_count,
            )
        if hasattr(usage, "thoughts_token_count"):
            span.set_data(
                SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
                usage.thoughts_token_count,
            )

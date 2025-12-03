import copy
import inspect
from functools import wraps
from .consts import ORIGIN, TOOL_ATTRIBUTES_MAP, GEN_AI_SYSTEM
from typing import (
    cast,
    TYPE_CHECKING,
    Iterable,
    Any,
    Callable,
    List,
    Optional,
    Union,
    TypedDict,
)

import sentry_sdk
from sentry_sdk.ai.utils import (
    set_data_normalized,
    truncate_and_annotate_messages,
    normalize_message_roles,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    safe_serialize,
)
from google.genai.types import GenerateContentConfig

if TYPE_CHECKING:
    from sentry_sdk.tracing import Span
    from google.genai.types import (
        GenerateContentResponse,
        ContentListUnion,
        Tool,
        Model,
        EmbedContentResponse,
    )


class UsageData(TypedDict):
    """Structure for token usage data."""

    input_tokens: int
    input_tokens_cached: int
    output_tokens: int
    output_tokens_reasoning: int
    total_tokens: int


def extract_usage_data(response):
    # type: (Union[GenerateContentResponse, dict[str, Any]]) -> UsageData
    """Extract usage data from response into a structured format.

    Args:
        response: The GenerateContentResponse object or dictionary containing usage metadata

    Returns:
        UsageData: Dictionary with input_tokens, input_tokens_cached,
                   output_tokens, and output_tokens_reasoning fields
    """
    usage_data = UsageData(
        input_tokens=0,
        input_tokens_cached=0,
        output_tokens=0,
        output_tokens_reasoning=0,
        total_tokens=0,
    )

    # Handle dictionary response (from streaming)
    if isinstance(response, dict):
        usage = response.get("usage_metadata", {})
        if not usage:
            return usage_data

        prompt_tokens = usage.get("prompt_token_count", 0) or 0
        tool_use_prompt_tokens = usage.get("tool_use_prompt_token_count", 0) or 0
        usage_data["input_tokens"] = prompt_tokens + tool_use_prompt_tokens

        cached_tokens = usage.get("cached_content_token_count", 0) or 0
        usage_data["input_tokens_cached"] = cached_tokens

        reasoning_tokens = usage.get("thoughts_token_count", 0) or 0
        usage_data["output_tokens_reasoning"] = reasoning_tokens

        candidates_tokens = usage.get("candidates_token_count", 0) or 0
        # python-genai reports output and reasoning tokens separately
        # reasoning should be sub-category of output tokens
        usage_data["output_tokens"] = candidates_tokens + reasoning_tokens

        total_tokens = usage.get("total_token_count", 0) or 0
        usage_data["total_tokens"] = total_tokens

        return usage_data

    if not hasattr(response, "usage_metadata"):
        return usage_data

    usage = response.usage_metadata

    # Input tokens include both prompt and tool use prompt tokens
    prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
    tool_use_prompt_tokens = getattr(usage, "tool_use_prompt_token_count", 0) or 0
    usage_data["input_tokens"] = prompt_tokens + tool_use_prompt_tokens

    # Cached input tokens
    cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
    usage_data["input_tokens_cached"] = cached_tokens

    # Reasoning tokens
    reasoning_tokens = getattr(usage, "thoughts_token_count", 0) or 0
    usage_data["output_tokens_reasoning"] = reasoning_tokens

    # output_tokens = candidates_tokens + reasoning_tokens
    # google-genai reports output and reasoning tokens separately
    candidates_tokens = getattr(usage, "candidates_token_count", 0) or 0
    usage_data["output_tokens"] = candidates_tokens + reasoning_tokens

    total_tokens = getattr(usage, "total_token_count", 0) or 0
    usage_data["total_tokens"] = total_tokens

    return usage_data


def _capture_exception(exc):
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


def extract_contents_text(contents):
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
            extracted = extract_contents_text(item)
            if extracted:
                texts.append(extracted)
        return " ".join(texts) if texts else None

    # Dictionary case
    if isinstance(contents, dict):
        if "text" in contents:
            return contents["text"]
        # Try to extract from parts if present in dict
        if "parts" in contents:
            return extract_contents_text(contents["parts"])

    # Content object with parts - recurse into parts
    if getattr(contents, "parts", None):
        return extract_contents_text(contents.parts)

    # Direct text attribute
    if hasattr(contents, "text"):
        return contents.text

    return None


def _format_tools_for_span(tools):
    # type: (Iterable[Tool | Callable[..., Any]]) -> Optional[List[dict[str, Any]]]
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
                if getattr(tool, attr_name, None):
                    formatted_tools.append(
                        {
                            "name": attr_name,
                            "description": description,
                        }
                    )
                    break

    return formatted_tools if formatted_tools else None


def extract_tool_calls(response):
    # type: (GenerateContentResponse) -> Optional[List[dict[str, Any]]]
    """Extract tool/function calls from response candidates and automatic function calling history."""

    tool_calls = []

    # Extract from candidates, sometimes tool calls are nested under the content.parts object
    if getattr(response, "candidates", []):
        for candidate in response.candidates:
            if not hasattr(candidate, "content") or not getattr(
                candidate.content, "parts", []
            ):
                continue

            for part in candidate.content.parts:
                if getattr(part, "function_call", None):
                    function_call = part.function_call
                    tool_call = {
                        "name": getattr(function_call, "name", None),
                        "type": "function_call",
                    }

                    # Extract arguments if available
                    if getattr(function_call, "args", None):
                        tool_call["arguments"] = safe_serialize(function_call.args)

                    tool_calls.append(tool_call)

    # Extract from automatic_function_calling_history
    # This is the history of tool calls made by the model
    if getattr(response, "automatic_function_calling_history", None):
        for content in response.automatic_function_calling_history:
            if not getattr(content, "parts", None):
                continue

            for part in getattr(content, "parts", []):
                if getattr(part, "function_call", None):
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
    # type: (tuple[Any, ...], dict[str, Any], Tool) -> dict[str, Any]
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
            # type: (Any, Any) -> Any
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
                    _capture_exception(exc)
                    raise

        return async_wrapped
    else:
        # Sync function
        @wraps(tool)
        def sync_wrapped(*args, **kwargs):
            # type: (Any, Any) -> Any
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
                    _capture_exception(exc)
                    raise

        return sync_wrapped


def wrapped_config_with_tools(config):
    # type: (GenerateContentConfig) -> GenerateContentConfig
    """Wrap tools in config to emit execute_tool spans. Tools are sometimes passed directly as
    callable functions as a part of the config object."""

    if not config or not getattr(config, "tools", None):
        return config

    result = copy.copy(config)
    result.tools = [wrapped_tool(tool) for tool in config.tools]

    return result


def _extract_response_text(response):
    # type: (GenerateContentResponse) -> Optional[List[str]]
    """Extract text from response candidates."""

    if not response or not getattr(response, "candidates", []):
        return None

    texts = []
    for candidate in response.candidates:
        if not hasattr(candidate, "content") or not hasattr(candidate.content, "parts"):
            continue

        for part in candidate.content.parts:
            if getattr(part, "text", None):
                texts.append(part.text)

    return texts if texts else None


def extract_finish_reasons(response):
    # type: (GenerateContentResponse) -> Optional[List[str]]
    """Extract finish reasons from response candidates."""
    if not response or not getattr(response, "candidates", []):
        return None

    finish_reasons = []
    for candidate in response.candidates:
        if getattr(candidate, "finish_reason", None):
            # Convert enum value to string if necessary
            reason = str(candidate.finish_reason)
            # Remove enum prefix if present (e.g., "FinishReason.STOP" -> "STOP")
            if "." in reason:
                reason = reason.split(".")[-1]
            finish_reasons.append(reason)

    return finish_reasons if finish_reasons else None


def set_span_data_for_request(span, integration, model, contents, kwargs):
    # type: (Span, Any, str, ContentListUnion, dict[str, Any]) -> None
    """Set span data for the request."""
    span.set_data(SPANDATA.GEN_AI_SYSTEM, GEN_AI_SYSTEM)
    span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)

    if kwargs.get("stream", False):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, True)

    config = kwargs.get("config")  # type: Optional[GenerateContentConfig]

    # Set input messages/prompts if PII is allowed
    if should_send_default_pii() and integration.include_prompts:
        messages = []

        # Add system instruction if present
        if config and hasattr(config, "system_instruction"):
            system_instruction = config.system_instruction
            if system_instruction:
                system_text = extract_contents_text(system_instruction)
                if system_text:
                    messages.append({"role": "system", "content": system_text})

        # Add user message
        contents_text = extract_contents_text(contents)
        if contents_text:
            messages.append({"role": "user", "content": contents_text})

        if messages:
            normalized_messages = normalize_message_roles(messages)
            scope = sentry_sdk.get_current_scope()
            messages_data = truncate_and_annotate_messages(
                normalized_messages, span, scope
            )
            if messages_data is not None:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_MESSAGES,
                    messages_data,
                    unpack=False,
                )

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

    # Set tools if available
    if config is not None and hasattr(config, "tools"):
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


def set_span_data_for_response(span, integration, response):
    # type: (Span, Any, GenerateContentResponse) -> None
    """Set span data for the response."""
    if not response:
        return

    if should_send_default_pii() and integration.include_prompts:
        response_texts = _extract_response_text(response)
        if response_texts:
            # Format as JSON string array as per documentation
            span.set_data(SPANDATA.GEN_AI_RESPONSE_TEXT, safe_serialize(response_texts))

    tool_calls = extract_tool_calls(response)
    if tool_calls:
        # Tool calls should be JSON serialized
        span.set_data(SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, safe_serialize(tool_calls))

    finish_reasons = extract_finish_reasons(response)
    if finish_reasons:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reasons
        )

    if getattr(response, "response_id", None):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_ID, response.response_id)

    if getattr(response, "model_version", None):
        span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, response.model_version)

    usage_data = extract_usage_data(response)

    if usage_data["input_tokens"]:
        span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage_data["input_tokens"])

    if usage_data["input_tokens_cached"]:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
            usage_data["input_tokens_cached"],
        )

    if usage_data["output_tokens"]:
        span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage_data["output_tokens"])

    if usage_data["output_tokens_reasoning"]:
        span.set_data(
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
            usage_data["output_tokens_reasoning"],
        )

    if usage_data["total_tokens"]:
        span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage_data["total_tokens"])


def prepare_generate_content_args(args, kwargs):
    # type: (tuple[Any, ...], dict[str, Any]) -> tuple[Any, Any, str]
    """Extract and prepare common arguments for generate_content methods."""
    model = args[0] if args else kwargs.get("model", "unknown")
    contents = args[1] if len(args) > 1 else kwargs.get("contents")
    model_name = get_model_name(model)

    config = kwargs.get("config")
    wrapped_config = wrapped_config_with_tools(config)
    if wrapped_config is not config:
        kwargs["config"] = wrapped_config

    return model, contents, model_name


def prepare_embed_content_args(args, kwargs):
    # type: (tuple[Any, ...], dict[str, Any]) -> tuple[str, Any]
    """Extract and prepare common arguments for embed_content methods.

    Returns:
        tuple: (model_name, contents)
    """
    model = kwargs.get("model", "unknown")
    contents = kwargs.get("contents")
    model_name = get_model_name(model)

    return model_name, contents


def set_span_data_for_embed_request(span, integration, contents, kwargs):
    # type: (Span, Any, Any, dict[str, Any]) -> None
    """Set span data for embedding request."""
    # Include input contents if PII is allowed
    if should_send_default_pii() and integration.include_prompts:
        if contents:
            # For embeddings, contents is typically a list of strings/texts
            input_texts = []

            # Handle various content formats
            if isinstance(contents, str):
                input_texts = [contents]
            elif isinstance(contents, list):
                for item in contents:
                    text = extract_contents_text(item)
                    if text:
                        input_texts.append(text)
            else:
                text = extract_contents_text(contents)
                if text:
                    input_texts = [text]

            if input_texts:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_EMBEDDINGS_INPUT,
                    input_texts,
                    unpack=False,
                )


def set_span_data_for_embed_response(span, integration, response):
    # type: (Span, Any, EmbedContentResponse) -> None
    """Set span data for embedding response."""
    if not response:
        return

    # Extract token counts from embeddings statistics (Vertex AI only)
    # Each embedding has its own statistics with token_count
    if hasattr(response, "embeddings") and response.embeddings:
        total_tokens = 0

        for embedding in response.embeddings:
            if hasattr(embedding, "statistics") and embedding.statistics:
                token_count = getattr(embedding.statistics, "token_count", None)
                if token_count is not None:
                    total_tokens += int(token_count)

        # Set token count if we found any
        if total_tokens > 0:
            span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, total_tokens)

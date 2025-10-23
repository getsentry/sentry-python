import sentry_sdk
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.utils import safe_serialize

from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_available_tools,
    _set_model_data,
    _should_send_prompts,
    _get_model_name,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, List, Dict
    from pydantic_ai.usage import RequestUsage  # type: ignore

try:
    from pydantic_ai.messages import (  # type: ignore
        BaseToolCallPart,
        BaseToolReturnPart,
        SystemPromptPart,
        UserPromptPart,
        TextPart,
        ThinkingPart,
    )
except ImportError:
    # Fallback if these classes are not available
    BaseToolCallPart = None
    BaseToolReturnPart = None
    SystemPromptPart = None
    UserPromptPart = None
    TextPart = None
    ThinkingPart = None


def _set_usage_data(span, usage):
    # type: (sentry_sdk.tracing.Span, RequestUsage) -> None
    """Set token usage data on a span."""
    if usage is None:
        return

    if hasattr(usage, "input_tokens") and usage.input_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens)

    if hasattr(usage, "output_tokens") and usage.output_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens)

    if hasattr(usage, "total_tokens") and usage.total_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_tokens)


def _set_input_messages(span, messages):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Set input messages data on a span."""
    if not _should_send_prompts():
        return

    if not messages:
        return

    try:
        formatted_messages = []
        system_prompt = None

        # Extract system prompt from any ModelRequest with instructions
        for msg in messages:
            if hasattr(msg, "instructions") and msg.instructions:
                system_prompt = msg.instructions
                break

        # Add system prompt as first message if present
        if system_prompt:
            formatted_messages.append(
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]}
            )

        for msg in messages:
            if hasattr(msg, "parts"):
                for part in msg.parts:
                    role = "user"
                    # Use isinstance checks with proper base classes
                    if SystemPromptPart and isinstance(part, SystemPromptPart):
                        role = "system"
                    elif (
                        (TextPart and isinstance(part, TextPart))
                        or (ThinkingPart and isinstance(part, ThinkingPart))
                        or (BaseToolCallPart and isinstance(part, BaseToolCallPart))
                    ):
                        role = "assistant"
                    elif BaseToolReturnPart and isinstance(part, BaseToolReturnPart):
                        role = "tool"

                    content = []  # type: List[Dict[str, Any] | str]
                    tool_calls = None
                    tool_call_id = None

                    # Handle ToolCallPart (assistant requesting tool use)
                    if BaseToolCallPart and isinstance(part, BaseToolCallPart):
                        tool_call_data = {}
                        if hasattr(part, "tool_name"):
                            tool_call_data["name"] = part.tool_name
                        if hasattr(part, "args"):
                            tool_call_data["arguments"] = safe_serialize(part.args)
                        if tool_call_data:
                            tool_calls = [tool_call_data]
                    # Handle ToolReturnPart (tool result)
                    elif BaseToolReturnPart and isinstance(part, BaseToolReturnPart):
                        if hasattr(part, "tool_name"):
                            tool_call_id = part.tool_name
                        if hasattr(part, "content"):
                            content.append({"type": "text", "text": str(part.content)})
                    # Handle regular content
                    elif hasattr(part, "content"):
                        if isinstance(part.content, str):
                            content.append({"type": "text", "text": part.content})
                        elif isinstance(part.content, list):
                            for item in part.content:
                                if isinstance(item, str):
                                    content.append({"type": "text", "text": item})
                                else:
                                    content.append(safe_serialize(item))
                        else:
                            content.append({"type": "text", "text": str(part.content)})

                    # Add message if we have content or tool calls
                    if content or tool_calls:
                        message = {"role": role}  # type: Dict[str, Any]
                        if content:
                            message["content"] = content
                        if tool_calls:
                            message["tool_calls"] = tool_calls
                        if tool_call_id:
                            message["tool_call_id"] = tool_call_id
                        formatted_messages.append(message)

        if formatted_messages:
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MESSAGES, formatted_messages, unpack=False
            )
    except Exception:
        # If we fail to format messages, just skip it
        pass


def _set_output_data(span, response):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Set output data on a span."""
    if not _should_send_prompts():
        return

    if not response:
        return

    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_MODEL, response.model_name)
    try:
        # Extract text from ModelResponse
        if hasattr(response, "parts"):
            texts = []
            tool_calls = []

            for part in response.parts:
                if TextPart and isinstance(part, TextPart) and hasattr(part, "content"):
                    texts.append(part.content)
                elif BaseToolCallPart and isinstance(part, BaseToolCallPart):
                    tool_call_data = {
                        "type": "function",
                    }
                    if hasattr(part, "tool_name"):
                        tool_call_data["name"] = part.tool_name
                    if hasattr(part, "args"):
                        tool_call_data["arguments"] = safe_serialize(part.args)
                    tool_calls.append(tool_call_data)

            if texts:
                set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, texts)

            if tool_calls:
                span.set_data(
                    SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, safe_serialize(tool_calls)
                )

    except Exception:
        # If we fail to format output, just skip it
        pass


def ai_client_span(messages, agent, model, model_settings):
    # type: (Any, Any, Any, Any) -> sentry_sdk.tracing.Span
    """Create a span for an AI client call (model request).

    Args:
        messages: Full conversation history (list of messages)
        agent: Agent object
        model: Model object
        model_settings: Model settings
    """
    # Determine model name for span name
    model_obj = model
    if agent and hasattr(agent, "model"):
        model_obj = agent.model

    model_name = _get_model_name(model_obj) or "unknown"

    span = sentry_sdk.start_span(
        op=OP.GEN_AI_CHAT,
        name=f"chat {model_name}",
        origin=SPAN_ORIGIN,
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    _set_agent_data(span, agent)
    _set_model_data(span, model, model_settings)

    # Set streaming flag
    agent_data = sentry_sdk.get_current_scope()._contexts.get("pydantic_ai_agent") or {}
    is_streaming = agent_data.get("_streaming", False)
    span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, is_streaming)

    # Add available tools if agent is available
    agent_obj = agent
    if not agent_obj:
        # Try to get from Sentry scope
        agent_data = (
            sentry_sdk.get_current_scope()._contexts.get("pydantic_ai_agent") or {}
        )
        agent_obj = agent_data.get("_agent")

    _set_available_tools(span, agent_obj)

    # Set input messages (full conversation history)
    if messages:
        _set_input_messages(span, messages)

    return span


def update_ai_client_span(span, model_response):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Update the AI client span with response data."""
    if not span:
        return

    # Set usage data if available
    if model_response and hasattr(model_response, "usage"):
        _set_usage_data(span, model_response.usage)

    # Set output data
    _set_output_data(span, model_response)

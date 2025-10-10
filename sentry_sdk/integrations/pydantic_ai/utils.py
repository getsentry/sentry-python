import sentry_sdk
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import set_span_errored
from sentry_sdk.utils import event_from_exception, safe_serialize

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from pydantic_ai.usage import RequestUsage

try:
    import pydantic_ai

except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


def _should_send_prompts():
    # type: () -> bool
    """
    Check if prompts should be sent to Sentry.

    This checks both send_default_pii and the include_prompts integration setting.
    """
    if not should_send_default_pii():
        return False

    # Get the integration instance from the client
    integration = sentry_sdk.get_client().get_integration(
        pydantic_ai.__name__.split(".")[0]
    )

    if integration is None:
        return False

    return getattr(integration, "include_prompts", True)


def _capture_exception(exc):
    # type: (Any) -> None
    set_span_errored()

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "pydantic_ai", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _get_model_name(model_obj):
    # type: (Any) -> str | None
    """Extract model name from a model object.

    Args:
        model_obj: Model object to extract name from

    Returns:
        Model name string or None if not found
    """
    if not model_obj:
        return None

    if hasattr(model_obj, "model_name"):
        return model_obj.model_name
    elif hasattr(model_obj, "name"):
        try:
            return model_obj.name()
        except Exception:
            return str(model_obj)
    elif isinstance(model_obj, str):
        return model_obj
    else:
        return str(model_obj)


def _set_agent_data(span, agent):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Set agent-related data on a span.

    Args:
        span: The span to set data on
        agent: Agent object (can be None, will try to get from Sentry scope if not provided)
    """
    # Extract agent name from agent object or Sentry scope
    agent_obj = agent
    if not agent_obj:
        # Try to get from Sentry scope
        agent_data = (
            sentry_sdk.get_current_scope()._contexts.get("pydantic_ai_agent") or {}
        )
        agent_obj = agent_data.get("_agent")

    if agent_obj and hasattr(agent_obj, "name") and agent_obj.name:
        span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent_obj.name)


def _set_model_data(span, model, model_settings):
    # type: (sentry_sdk.tracing.Span, Any, Any) -> None
    """Set model-related data on a span.

    Args:
        span: The span to set data on
        model: Model object (can be None, will try to get from agent if not provided)
        model_settings: Model settings (can be None, will try to get from agent if not provided)
    """
    # Try to get agent from Sentry scope if we need it
    agent_data = sentry_sdk.get_current_scope()._contexts.get("pydantic_ai_agent") or {}
    agent_obj = agent_data.get("_agent")

    # Extract model information
    model_obj = model
    if not model_obj and agent_obj and hasattr(agent_obj, "model"):
        model_obj = agent_obj.model

    if model_obj:
        # Set system from model
        if hasattr(model_obj, "system"):
            span.set_data(SPANDATA.GEN_AI_SYSTEM, model_obj.system)

        # Set model name
        model_name = _get_model_name(model_obj)
        if model_name:
            span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_name)

    # Extract model settings
    settings = model_settings
    if not settings and agent_obj and hasattr(agent_obj, "model_settings"):
        settings = agent_obj.model_settings

    if settings:
        settings_map = {
            "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
            "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
        }

        # ModelSettings is a TypedDict (dict at runtime), so use dict access
        if isinstance(settings, dict):
            for setting_name, spandata_key in settings_map.items():
                value = settings.get(setting_name)
                if value is not None:
                    span.set_data(spandata_key, value)
        else:
            # Fallback for object-style settings
            for setting_name, spandata_key in settings_map.items():
                if hasattr(settings, setting_name):
                    value = getattr(settings, setting_name)
                    if value is not None:
                        span.set_data(spandata_key, value)


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
                    if hasattr(part, "__class__"):
                        if "System" in part.__class__.__name__:
                            role = "system"
                        elif (
                            "Assistant" in part.__class__.__name__
                            or "Text" in part.__class__.__name__
                            or "ToolCall" in part.__class__.__name__
                        ):
                            role = "assistant"
                        elif "ToolReturn" in part.__class__.__name__:
                            role = "tool"

                    content = []
                    tool_calls = None
                    tool_call_id = None

                    # Handle ToolCallPart (assistant requesting tool use)
                    if "ToolCall" in part.__class__.__name__:
                        tool_call_data = {}
                        if hasattr(part, "tool_name"):
                            tool_call_data["name"] = part.tool_name
                        if hasattr(part, "args"):
                            tool_call_data["arguments"] = safe_serialize(part.args)
                        if tool_call_data:
                            tool_calls = [tool_call_data]
                    # Handle ToolReturnPart (tool result)
                    elif "ToolReturn" in part.__class__.__name__:
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
                        message = {"role": role}
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
                if hasattr(part, "__class__"):
                    if "Text" in part.__class__.__name__ and hasattr(part, "content"):
                        texts.append(part.content)
                    elif "ToolCall" in part.__class__.__name__:
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

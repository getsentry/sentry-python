import sentry_sdk
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import safe_serialize

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, List, Dict
    from pydantic_ai.usage import RequestUsage  # type: ignore


def _should_send_prompts():
    # type: () -> bool
    """
    Check if prompts should be sent to Sentry.

    This checks both send_default_pii and the include_prompts integration setting.
    """
    if not should_send_default_pii():
        return False

    from . import PydanticAIIntegration

    # Get the integration instance from the client
    integration = sentry_sdk.get_client().get_integration(PydanticAIIntegration)

    if integration is None:
        return False

    return getattr(integration, "include_prompts", False)


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


def _set_available_tools(span, agent):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Set available tools data on a span from an agent's function toolset.

    Args:
        span: The span to set data on
        agent: Agent object with _function_toolset attribute
    """
    if not agent or not hasattr(agent, "_function_toolset"):
        return

    try:
        tools = []
        # Get tools from the function toolset
        if hasattr(agent._function_toolset, "tools"):
            for tool_name, tool in agent._function_toolset.tools.items():
                tool_info = {"name": tool_name}

                # Add description from function_schema if available
                if hasattr(tool, "function_schema"):
                    schema = tool.function_schema
                    if getattr(schema, "description", None):
                        tool_info["description"] = schema.description

                    # Add parameters from json_schema
                    if getattr(schema, "json_schema", None):
                        tool_info["parameters"] = schema.json_schema

                tools.append(tool_info)

        if tools:
            span.set_data(
                SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, safe_serialize(tools)
            )
    except Exception:
        # If we can't extract tools, just skip it
        pass

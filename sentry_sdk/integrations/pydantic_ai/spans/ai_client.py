import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_model_data,
    _set_usage_data,
    _set_input_messages,
    _set_output_data,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def ai_client_span(model_request, agent, model, model_settings):
    # type: (Any, Any, Any, Any) -> sentry_sdk.tracing.Span
    """Create a span for an AI client call (model request)."""
    # Determine model name for span description
    model_obj = model
    if agent and hasattr(agent, "model"):
        model_obj = agent.model

    model_name = "unknown"
    if model_obj:
        if hasattr(model_obj, "model_name"):
            model_name = model_obj.model_name
        elif isinstance(model_obj, str):
            model_name = model_obj
        else:
            model_name = str(model_obj)

    span = sentry_sdk.start_span(
        op=OP.GEN_AI_CHAT,
        description=f"chat {model_name}",
        origin=SPAN_ORIGIN,
    )

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    _set_agent_data(span, agent)
    _set_model_data(span, model, model_settings)

    # Add available tools if agent is available
    agent_obj = agent
    if not agent_obj:
        # Try to get from Sentry scope
        agent_data = (
            sentry_sdk.get_current_scope()._contexts.get("pydantic_ai_agent") or {}
        )
        agent_obj = agent_data.get("_agent")

    if agent_obj and hasattr(agent_obj, "_function_toolset"):
        try:
            from sentry_sdk.utils import safe_serialize

            tools = []
            # Get tools from the function toolset
            if hasattr(agent_obj._function_toolset, "tools"):
                for tool_name, tool in agent_obj._function_toolset.tools.items():
                    tool_info = {"name": tool_name}

                    # Add description from function_schema if available
                    if hasattr(tool, "function_schema"):
                        schema = tool.function_schema
                        if hasattr(schema, "description") and schema.description:
                            tool_info["description"] = schema.description

                        # Add parameters from json_schema
                        if hasattr(schema, "json_schema") and schema.json_schema:
                            tool_info["parameters"] = schema.json_schema

                    tools.append(tool_info)

            if tools:
                span.set_data(
                    SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, safe_serialize(tools)
                )
        except Exception:
            # If we can't extract tools, just skip it
            pass

    # Set input messages if available
    if model_request and hasattr(model_request, "parts"):
        _set_input_messages(span, [model_request])

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

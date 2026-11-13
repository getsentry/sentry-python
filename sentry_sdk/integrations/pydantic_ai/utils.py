from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.traces import Span
from sentry_sdk.utils import (
    event_from_exception,
    safe_serialize,
)

if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from pydantic_ai import Agent
    from pydantic_ai.models import AbstractModel, Model


def _set_agent_data(span: "Span", agent: "Optional[Agent]") -> None:
    """Set agent-related data on a span.

    Args:
        span: The span to set data on
        agent: Agent object
    """
    if agent and hasattr(agent, "name") and agent.name:
        span.set_attribute(SPANDATA.GEN_AI_AGENT_NAME, agent.name)


def _get_model_name(
    model_obj: "Optional[Union[AbstractModel, Model, str]]",
) -> "Optional[str]":
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


def _set_available_tools(span: "Span", agent: "Optional[Agent[Any, Any]]") -> None:
    """Set available tools data on a span from an agent's function toolset.

    Args:
        span: The span to set data on
        agent: Agent object with _function_toolset attribute
    """
    if not agent or not hasattr(agent, "_function_toolset"):
        return

    client_options = sentry_sdk.get_client().options
    if not client_options["data_collection"]["gen_ai"]["inputs"]:
        return

    try:
        tools = []
        # Get tools from the function toolset
        if hasattr(agent._function_toolset, "tools"):
            for tool_name, tool in agent._function_toolset.tools.items():
                tool_info: "dict[str, Any]" = {"name": tool_name}

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
            span.set_attribute(
                SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, safe_serialize(tools)
            )

    except Exception:
        # If we can't extract tools, just skip it
        pass


def _capture_exception(exc: "Any", handled: bool = False) -> None:
    event, hint = event_from_exception(
        exc,
        mechanism={"type": "pydantic_ai", "handled": handled},
    )
    sentry_sdk.capture_event(event, hint=hint)

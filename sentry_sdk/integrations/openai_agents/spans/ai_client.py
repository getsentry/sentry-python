import json
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import has_span_streaming_enabled

try:
    from agents import (
        CodeInterpreterTool,
        ComputerTool,
        FileSearchTool,
        FunctionTool,
        HostedMCPTool,
        ImageGenerationTool,
        LocalShellTool,
        WebSearchTool,
    )
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")

try:
    from agents import ApplyPatchTool, ShellTool
except ImportError:
    ShellTool = None
    ApplyPatchTool = None

try:
    from agents import ToolSearchTool
except ImportError:
    ToolSearchTool = None

try:
    from agents import CustomTool
except ImportError:
    CustomTool = None

try:
    from agents import ProgrammaticToolCallingTool
except ImportError:
    ProgrammaticToolCallingTool = None


from ..consts import SPAN_ORIGIN
from ..utils import (
    _set_agent_data,
    _set_input_data,
    _set_output_data,
    _set_usage_data,
)

if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from agents import Agent, Tool

    from sentry_sdk._types import ToolDefinition


def _transform_tool_definitions(tools: "list[Tool]") -> "list[ToolDefinition]":
    """
    Transform tool definitions to the schema used by the "gen_ai.tool.definitions" attribute.
    Includes special handling for tools where the type includes a name, description or parameters.

    Note: These are generated from the same OpenAPI spec as Responses API tools.
    """
    if not isinstance(tools, list):
        return []

    tool_definitions: "list[ToolDefinition]" = []
    for tool in tools:
        if isinstance(tool, FunctionTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.params_json_schema,
                }
            )
            continue

        if isinstance(tool, WebSearchTool):
            tool_definitions.append(
                {
                    "type": "web_search",
                    "name": tool.name,
                }
            )
            continue

        if isinstance(tool, FileSearchTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if isinstance(tool, ComputerTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if CustomTool is not None and isinstance(tool, CustomTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                }
            )
            continue

        if isinstance(tool, HostedMCPTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if ApplyPatchTool is not None and isinstance(tool, ApplyPatchTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if ShellTool is not None and isinstance(tool, ShellTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if isinstance(tool, ImageGenerationTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if isinstance(tool, CodeInterpreterTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if isinstance(tool, LocalShellTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if ToolSearchTool is not None and isinstance(tool, ToolSearchTool):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

        if ProgrammaticToolCallingTool is not None and isinstance(
            tool, ProgrammaticToolCallingTool
        ):
            tool_definitions.append(
                {
                    "type": "function",
                    "name": tool.name,
                }
            )
            continue

    return tool_definitions


def ai_client_span(
    agent: "Agent", get_response_kwargs: "dict[str, Any]"
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    # TODO-anton: implement other types of operations. Now "chat" is hardcoded.
    # Get model name from agent.model or fall back to request model (for when agent.model is None/default)
    model_name = None
    if agent.model:
        model_name = agent.model.model if hasattr(agent.model, "model") else agent.model
    elif hasattr(agent, "_sentry_request_model"):
        model_name = agent._sentry_request_model

    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        span = sentry_sdk.traces.start_span(
            name=f"chat {model_name}",
            attributes={
                "sentry.op": OP.GEN_AI_CHAT,
                "sentry.origin": SPAN_ORIGIN,
                SPANDATA.GEN_AI_OPERATION_NAME: "chat",
            },
        )

        set_on_span = span.set_attribute
    else:
        span = sentry_sdk.start_span(
            op=OP.GEN_AI_CHAT,
            name=f"chat {model_name}",
            origin=SPAN_ORIGIN,
        )
        # TODO-anton: remove hardcoded stuff and replace something that also works for embedding and so on
        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

        set_on_span = span.set_data

    _set_agent_data(span, agent)

    set_on_span(
        SPANDATA.GEN_AI_TOOL_DEFINITIONS,
        json.dumps(_transform_tool_definitions(agent.tools)),
    )

    _set_input_data(span, get_response_kwargs)

    return span


def update_ai_client_span(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    response: "Any",
    response_model: "Optional[str]" = None,
    agent: "Optional[Agent]" = None,
) -> None:
    """Update AI client span with response data (works for streaming and non-streaming)."""
    if hasattr(response, "usage") and response.usage:
        _set_usage_data(span, response.usage)

    if hasattr(response, "output") and response.output:
        _set_output_data(span, response)

    set_on_span = (
        span.set_attribute if isinstance(span, StreamedSpan) else span.set_data
    )

    if response_model is not None:
        set_on_span(SPANDATA.GEN_AI_RESPONSE_MODEL, response_model)
    elif hasattr(response, "model") and response.model:
        set_on_span(SPANDATA.GEN_AI_RESPONSE_MODEL, str(response.model))

    # Set conversation ID from agent if available
    if agent:
        conv_id = getattr(agent, "_sentry_conversation_id", None)
        if conv_id:
            set_on_span(SPANDATA.GEN_AI_CONVERSATION_ID, conv_id)

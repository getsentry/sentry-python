"""Span creation and data population for the pydantic-ai integration.

Functions here consume the plain data structures returned by _extract and
write them onto Sentry spans; they contain no pydantic-ai object probing of
their own.
"""

import json
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import (
    _set_span_data_attribute,
    get_start_span_function,
    normalize_message_roles,
    set_data_normalized,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import (
    has_span_streaming_enabled,
    should_truncate_gen_ai_input,
)
from sentry_sdk.utils import event_from_exception, safe_serialize

from ._extract import (
    MODEL_SETTINGS_TO_SPANDATA,
    ModelInfo,
    extract_agent_name,
    extract_agent_prompt_messages,
    extract_available_tools,
    extract_model_info,
    extract_request_messages,
    extract_response_model_name,
    extract_response_parts,
    extract_system_instructions,
    extract_usage_kwargs,
)
from ._run_context import get_current_agent, get_is_streaming
from .consts import SPAN_ORIGIN

if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from pydantic_ai._tool_manager import ToolDefinition  # type: ignore
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.usage import RequestUsage, RunUsage

    from sentry_sdk.traces import StreamedSpan


def _should_send_prompts() -> bool:
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


def _capture_exception(exc: "Any", handled: bool = False) -> None:
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "pydantic_ai", "handled": handled},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _set_agent_data(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]", agent: "Any"
) -> None:
    """Set agent-related data on a span.

    Args:
        span: The span to set data on
        agent: Agent object (can be None, will try to get from contextvar if not provided)
    """
    # Extract agent name from agent object or contextvar
    agent_name = extract_agent_name(agent or get_current_agent())
    if agent_name:
        _set_span_data_attribute(span, SPANDATA.GEN_AI_AGENT_NAME, agent_name)


def _set_model_data(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    model: "Any",
    model_settings: "Any",
    agent: "Any" = None,
    model_info: "Optional[ModelInfo]" = None,
) -> None:
    """Set model-related data on a span.

    Args:
        span: The span to set data on
        model: Model object (can be None, will try to get from agent if not provided)
        model_settings: Model settings (can be None, will try to get from agent if not provided)
        agent: Agent to fall back to for model and settings (defaults to the
            agent of the current run)
        model_info: Already-extracted model info; passing it avoids a second
            extraction when the caller needed it anyway
    """
    if model_info is None:
        model_info = extract_model_info(
            model, model_settings, agent or get_current_agent()
        )

    if model_info.system is not None:
        _set_span_data_attribute(span, SPANDATA.GEN_AI_SYSTEM, model_info.system)

    if model_info.name:
        _set_span_data_attribute(span, SPANDATA.GEN_AI_REQUEST_MODEL, model_info.name)

    for setting_name, value in model_info.settings.items():
        spandata_key = MODEL_SETTINGS_TO_SPANDATA.get(setting_name)
        if spandata_key is not None:
            _set_span_data_attribute(span, spandata_key, value)


def _set_available_tools(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]", agent: "Any"
) -> None:
    """Set available tools data on a span from an agent's function toolset.

    Args:
        span: The span to set data on
        agent: Agent object with _function_toolset attribute
    """
    tools = extract_available_tools(agent)
    if tools:
        _set_span_data_attribute(
            span, SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, safe_serialize(tools)
        )


def _set_request_messages_data(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    messages: "list[dict[str, Any]]",
) -> None:
    """Normalize, truncate if configured, and set gen_ai.request.messages."""
    normalized_messages = normalize_message_roles(messages)
    client = sentry_sdk.get_client()
    scope = sentry_sdk.get_current_scope()
    messages_data = (
        truncate_and_annotate_messages(normalized_messages, span, scope)
        if should_truncate_gen_ai_input(client.options)
        else normalized_messages
    )
    set_data_normalized(
        span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages_data, unpack=False
    )


def _set_usage_data(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    usage: "Union[RequestUsage, RunUsage]",
) -> None:
    """Set token usage data on a span.

    This function works with both RequestUsage (single request) and
    RunUsage (agent run) objects from pydantic_ai.

    Args:
        span: The Sentry span to set data on.
        usage: RequestUsage or RunUsage object containing token usage information.
    """
    usage_kwargs = extract_usage_kwargs(usage)
    if usage_kwargs is None:
        return

    record_token_usage(span, **usage_kwargs)


def _set_input_messages(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]", messages: "Any"
) -> None:
    """Set input messages data on a span."""
    if not _should_send_prompts():
        return

    if not messages:
        return

    try:
        system_instructions = extract_system_instructions(messages)
        if system_instructions:
            _set_span_data_attribute(
                span,
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
                json.dumps(system_instructions),
            )

        formatted_messages = extract_request_messages(messages)

        if formatted_messages:
            _set_request_messages_data(span, formatted_messages)
    except Exception:
        # If we fail to format messages, just skip it
        pass


def _set_output_data(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    response: "Optional[ModelResponse]",
) -> None:
    """Set output data on a span."""
    if not _should_send_prompts():
        return

    if not response:
        return

    if response.model_name:
        _set_span_data_attribute(
            span, SPANDATA.GEN_AI_RESPONSE_MODEL, response.model_name
        )

    try:
        parts = extract_response_parts(response)
        if parts:
            _set_span_data_attribute(
                span,
                SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                json.dumps([{"role": "assistant", "parts": parts}]),
            )
    except Exception:
        # If we fail to format output, just skip it
        pass


def ai_client_span(
    messages: "Any", agent: "Any", model: "Any", model_settings: "Any"
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    """Create a span for an AI client call (model request).

    Args:
        messages: Full conversation history (list of messages)
        agent: Agent object
        model: Model object
        model_settings: Model settings
    """
    # Resolve the agent and model info once so the span name and every
    # attribute derived below (gen_ai.request.model, agent data, available
    # tools) agree
    agent_obj = agent or get_current_agent()
    model_info = extract_model_info(model, model_settings, agent_obj)
    model_name = model_info.name or "unknown"

    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        span = sentry_sdk.traces.start_span(
            name=f"chat {model_name}",
            attributes={
                "sentry.op": OP.GEN_AI_CHAT,
                "sentry.origin": SPAN_ORIGIN,
                SPANDATA.GEN_AI_OPERATION_NAME: "chat",
                SPANDATA.GEN_AI_RESPONSE_STREAMING: get_is_streaming(),
            },
        )
    else:
        span = sentry_sdk.start_span(
            op=OP.GEN_AI_CHAT,
            name=f"chat {model_name}",
            origin=SPAN_ORIGIN,
        )

        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
        # Set streaming flag from contextvar
        span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, get_is_streaming())

    _set_agent_data(span, agent_obj)
    _set_model_data(span, model, model_settings, model_info=model_info)

    # Add available tools if agent is available
    _set_available_tools(span, agent_obj)

    # Set input messages (full conversation history)
    if messages:
        _set_input_messages(span, messages)

    return span


def update_ai_client_span(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    model_response: "Optional[ModelResponse]",
) -> None:
    """Update the AI client span with response data."""
    if not span:
        return

    # Set usage data if available
    if model_response and hasattr(model_response, "usage"):
        _set_usage_data(span, model_response.usage)

    # Set output data
    _set_output_data(span, model_response)


def invoke_agent_span(
    user_prompt: "Any",
    agent: "Any",
    model: "Any",
    model_settings: "Any",
    is_streaming: bool = False,
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    """Create a span for invoking the agent."""
    # Determine agent name for span
    name = extract_agent_name(agent) or "agent"

    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        span = sentry_sdk.traces.start_span(
            name=f"invoke_agent {name}",
            attributes={
                "sentry.op": OP.GEN_AI_INVOKE_AGENT,
                "sentry.origin": SPAN_ORIGIN,
                SPANDATA.GEN_AI_OPERATION_NAME: "invoke_agent",
            },
        )
    else:
        span = get_start_span_function()(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=f"invoke_agent {name}",
            origin=SPAN_ORIGIN,
        )

        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    _set_agent_data(span, agent)
    _set_model_data(span, model, model_settings, agent=agent)
    _set_available_tools(span, agent)

    # Add user prompt and system prompts if available and prompts are enabled
    if _should_send_prompts():
        messages = extract_agent_prompt_messages(agent, user_prompt)

        if messages:
            _set_request_messages_data(span, messages)

    return span


def update_invoke_agent_span(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]",
    result: "Any",
) -> None:
    """Update and close the invoke agent span."""
    if not span or not result:
        return

    # Extract output from result
    output = getattr(result, "output", None)

    # Set response text if prompts are enabled
    if _should_send_prompts() and output:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, str(output), unpack=False
        )

    # Set model name from response if available
    response_model_name = extract_response_model_name(result)
    if response_model_name:
        _set_span_data_attribute(
            span, SPANDATA.GEN_AI_RESPONSE_MODEL, response_model_name
        )


def execute_tool_span(
    tool_name: str,
    tool_args: "Any",
    agent: "Any",
    tool_definition: "Optional[ToolDefinition]" = None,
) -> "Union[sentry_sdk.tracing.Span, StreamedSpan]":
    """Create a span for tool execution.

    Args:
        tool_name: The name of the tool being executed
        tool_args: The arguments passed to the tool
        agent: The agent executing the tool
        tool_definition: The definition of the tool, if available
    """
    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
    if span_streaming:
        # Both keys must be present at span start so that attribute-based
        # ignore_spans / traces_sampler rules can match this span.
        span = sentry_sdk.traces.start_span(
            name=f"execute_tool {tool_name}",
            attributes={
                "sentry.op": OP.GEN_AI_EXECUTE_TOOL,
                "sentry.origin": SPAN_ORIGIN,
                SPANDATA.GEN_AI_OPERATION_NAME: "execute_tool",
                SPANDATA.GEN_AI_TOOL_NAME: tool_name,
            },
        )
    else:
        span = sentry_sdk.start_span(
            op=OP.GEN_AI_EXECUTE_TOOL,
            name=f"execute_tool {tool_name}",
            origin=SPAN_ORIGIN,
        )

        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
        span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool_name)

    if tool_definition is not None and hasattr(tool_definition, "description"):
        _set_span_data_attribute(
            span,
            SPANDATA.GEN_AI_TOOL_DESCRIPTION,
            tool_definition.description,
        )

    _set_agent_data(span, agent)

    if _should_send_prompts() and tool_args is not None:
        _set_span_data_attribute(
            span, SPANDATA.GEN_AI_TOOL_INPUT, safe_serialize(tool_args)
        )

    return span


def update_execute_tool_span(
    span: "Union[sentry_sdk.tracing.Span, StreamedSpan]", result: "Any"
) -> None:
    """Update the execute tool span with the result."""
    if not span:
        return

    if not _should_send_prompts() or result is None:
        return

    _set_span_data_attribute(span, SPANDATA.GEN_AI_TOOL_OUTPUT, safe_serialize(result))

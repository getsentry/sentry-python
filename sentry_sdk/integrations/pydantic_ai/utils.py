from contextvars import ContextVar
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import _set_span_data_attribute
from sentry_sdk.consts import SPANDATA
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import event_from_exception, safe_serialize

from ._extract import extract_agent_name, extract_available_tools, extract_model_info

if TYPE_CHECKING:
    from typing import Any, Union

    from sentry_sdk.traces import StreamedSpan


_MODEL_SETTINGS_SPANDATA = {
    "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
    "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
    "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
    "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
    "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
}


# Store the current agent context in a contextvar for re-entrant safety
# Using a list as a stack to support nested agent calls
_agent_context_stack: "ContextVar[list[dict[str, Any]]]" = ContextVar(
    "pydantic_ai_agent_context_stack", default=[]
)


def push_agent(agent: "Any", is_streaming: bool = False) -> None:
    """Push an agent context onto the stack along with its streaming flag."""
    stack = _agent_context_stack.get().copy()
    stack.append({"agent": agent, "is_streaming": is_streaming})
    _agent_context_stack.set(stack)


def pop_agent() -> None:
    """Pop an agent context from the stack."""
    stack = _agent_context_stack.get().copy()
    if stack:
        stack.pop()
    _agent_context_stack.set(stack)


def get_current_agent() -> "Any":
    """Get the current agent from the contextvar stack."""
    stack = _agent_context_stack.get()
    if stack:
        return stack[-1]["agent"]
    return None


def get_is_streaming() -> bool:
    """Get the streaming flag from the contextvar stack."""
    stack = _agent_context_stack.get()
    if stack:
        return stack[-1].get("is_streaming", False)
    return False


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
) -> None:
    """Set model-related data on a span.

    Args:
        span: The span to set data on
        model: Model object (can be None, will try to get from agent if not provided)
        model_settings: Model settings (can be None, will try to get from agent if not provided)
    """
    model_info = extract_model_info(model, model_settings, get_current_agent())

    if model_info.system is not None:
        _set_span_data_attribute(span, SPANDATA.GEN_AI_SYSTEM, model_info.system)

    if model_info.name:
        _set_span_data_attribute(span, SPANDATA.GEN_AI_REQUEST_MODEL, model_info.name)

    for setting_name, value in model_info.settings.items():
        spandata_key = _MODEL_SETTINGS_SPANDATA.get(setting_name)
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


def _capture_exception(exc: "Any", handled: bool = False) -> None:
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "pydantic_ai", "handled": handled},
    )
    sentry_sdk.capture_event(event, hint=hint)

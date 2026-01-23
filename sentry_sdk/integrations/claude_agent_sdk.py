from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import set_data_normalized, get_start_span_function
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations import _check_minimum_version, DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    package_version,
)
from sentry_sdk.tracing_utils import set_span_errored

try:
    import claude_agent_sdk
    from claude_agent_sdk import (
        query as original_query,
        ClaudeSDKClient,
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
    )
except ImportError:
    raise DidNotEnable("claude-agent-sdk not installed")

if TYPE_CHECKING:
    from typing import Any, AsyncGenerator, Optional
    from sentry_sdk.tracing import Span

AGENT_NAME = "claude-agent"
GEN_AI_SYSTEM = "claude-agent-sdk-python"


class ClaudeAgentSDKIntegration(Integration):
    identifier = "claude_agent_sdk"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts: bool = True) -> None:
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once() -> None:
        version = package_version("claude_agent_sdk")
        _check_minimum_version(ClaudeAgentSDKIntegration, version)
        claude_agent_sdk.query = _wrap_query(original_query)
        ClaudeSDKClient.query = _wrap_client_query(ClaudeSDKClient.query)
        ClaudeSDKClient.receive_response = _wrap_receive_response(
            ClaudeSDKClient.receive_response
        )


def _should_include_prompts(integration: "ClaudeAgentSDKIntegration") -> bool:
    return should_send_default_pii() and integration.include_prompts


def _capture_exception(exc: "Any") -> None:
    set_span_errored()
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "claude_agent_sdk", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _set_span_input_data(
    span: "Span",
    prompt: str,
    options: "Optional[Any]",
    integration: "ClaudeAgentSDKIntegration",
) -> None:
    set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, GEN_AI_SYSTEM)
    set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    if options is not None:
        model = getattr(options, "model", None)
        if model:
            set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, model)

        allowed_tools = getattr(options, "allowed_tools", None)
        if allowed_tools:
            tools_list = [{"name": tool} for tool in allowed_tools]
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, tools_list, unpack=False
            )

    if _should_include_prompts(integration):
        messages = []
        system_prompt = getattr(options, "system_prompt", None) if options else None
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        set_data_normalized(
            span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages, unpack=False
        )


def _extract_text_from_message(message: "Any") -> "Optional[str]":
    if not isinstance(message, AssistantMessage):
        return None
    text_parts = [
        block.text
        for block in getattr(message, "content", [])
        if isinstance(block, TextBlock) and hasattr(block, "text")
    ]
    return "".join(text_parts) if text_parts else None


def _extract_tool_calls(message: "Any") -> "Optional[list]":
    if not isinstance(message, AssistantMessage):
        return None
    tool_calls = []
    for block in getattr(message, "content", []):
        if isinstance(block, ToolUseBlock):
            tool_call = {"name": getattr(block, "name", "unknown")}
            tool_input = getattr(block, "input", None)
            if tool_input is not None:
                tool_call["input"] = tool_input
            tool_calls.append(tool_call)
    return tool_calls or None


def _extract_message_data(messages: list) -> dict:
    """Extract relevant data from a list of messages."""
    data = {
        "response_texts": [],
        "tool_calls": [],
        "total_cost": None,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "response_model": None,
    }

    for message in messages:
        if isinstance(message, AssistantMessage):
            text = _extract_text_from_message(message)
            if text:
                data["response_texts"].append(text)

            calls = _extract_tool_calls(message)
            if calls:
                data["tool_calls"].extend(calls)

            if not data["response_model"]:
                data["response_model"] = getattr(message, "model", None)

        elif isinstance(message, ResultMessage):
            data["total_cost"] = getattr(message, "total_cost_usd", None)
            usage = getattr(message, "usage", None)
            if isinstance(usage, dict):
                data["input_tokens"] = usage.get("input_tokens")
                data["output_tokens"] = usage.get("output_tokens")
                # Store cached tokens separately for the backend to apply discount pricing
                cached_input = usage.get("cache_read_input_tokens") or 0
                data["cached_input_tokens"] = cached_input if cached_input > 0 else None

    return data


def _set_span_output_data(
    span: "Span",
    messages: list,
    integration: "ClaudeAgentSDKIntegration",
) -> None:
    data = _extract_message_data(messages)

    if data["response_model"]:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_MODEL, data["response_model"]
        )
        if SPANDATA.GEN_AI_REQUEST_MODEL not in getattr(span, "_data", {}):
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MODEL, data["response_model"]
            )

    if _should_include_prompts(integration):
        if data["response_texts"]:
            set_data_normalized(
                span, SPANDATA.GEN_AI_RESPONSE_TEXT, data["response_texts"]
            )
        if data["tool_calls"]:
            set_data_normalized(
                span,
                SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
                data["tool_calls"],
                unpack=False,
            )

    if data["input_tokens"] is not None or data["output_tokens"] is not None:
        record_token_usage(
            span,
            input_tokens=data["input_tokens"],
            input_tokens_cached=data["cached_input_tokens"],
            output_tokens=data["output_tokens"],
        )

    if data["total_cost"] is not None:
        span.set_data("claude_code.total_cost_usd", data["total_cost"])


def _start_invoke_agent_span(
    prompt: str,
    options: "Optional[Any]",
    integration: "ClaudeAgentSDKIntegration",
) -> "Span":
    span = get_start_span_function()(
        op=OP.GEN_AI_INVOKE_AGENT,
        name=f"invoke_agent {AGENT_NAME}",
        origin=ClaudeAgentSDKIntegration.origin,
    )
    span.__enter__()

    set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
    set_data_normalized(span, SPANDATA.GEN_AI_AGENT_NAME, AGENT_NAME)
    set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, GEN_AI_SYSTEM)

    if _should_include_prompts(integration):
        messages = []
        system_prompt = getattr(options, "system_prompt", None) if options else None
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        set_data_normalized(
            span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages, unpack=False
        )

    return span


def _end_invoke_agent_span(
    span: "Span",
    messages: list,
    integration: "ClaudeAgentSDKIntegration",
) -> None:
    data = _extract_message_data(messages)

    if _should_include_prompts(integration) and data["response_texts"]:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, data["response_texts"])

    if data["response_model"]:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_MODEL, data["response_model"]
        )
        set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, data["response_model"])

    if data["input_tokens"] is not None or data["output_tokens"] is not None:
        record_token_usage(
            span,
            input_tokens=data["input_tokens"],
            input_tokens_cached=data["cached_input_tokens"],
            output_tokens=data["output_tokens"],
        )

    if data["total_cost"] is not None:
        span.set_data("claude_code.total_cost_usd", data["total_cost"])

    span.__exit__(None, None, None)


def _create_execute_tool_span(
    tool_use: "ToolUseBlock",
    tool_result: "Optional[ToolResultBlock]",
    integration: "ClaudeAgentSDKIntegration",
) -> "Span":
    tool_name = getattr(tool_use, "name", "unknown")
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_EXECUTE_TOOL,
        name=f"execute_tool {tool_name}",
        origin=ClaudeAgentSDKIntegration.origin,
    )

    set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
    set_data_normalized(span, SPANDATA.GEN_AI_TOOL_NAME, tool_name)
    set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, GEN_AI_SYSTEM)

    if _should_include_prompts(integration):
        tool_input = getattr(tool_use, "input", None)
        if tool_input is not None:
            set_data_normalized(span, SPANDATA.GEN_AI_TOOL_INPUT, tool_input)

        if tool_result is not None:
            tool_output = getattr(tool_result, "content", None)
            if tool_output is not None:
                set_data_normalized(span, SPANDATA.GEN_AI_TOOL_OUTPUT, tool_output)

    if tool_result is not None and getattr(tool_result, "is_error", False):
        span.set_status(SPANSTATUS.INTERNAL_ERROR)

    return span


def _process_tool_executions(
    messages: list, integration: "ClaudeAgentSDKIntegration"
) -> list:
    """Create execute_tool spans for tool executions found in messages.

    Returns a list of the created spans (for testing purposes).
    """
    tool_uses = {}
    tool_results = {}

    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for block in getattr(message, "content", []):
            if isinstance(block, ToolUseBlock):
                tool_id = getattr(block, "id", None)
                if tool_id:
                    tool_uses[tool_id] = block
            elif isinstance(block, ToolResultBlock):
                tool_use_id = getattr(block, "tool_use_id", None)
                if tool_use_id:
                    tool_results[tool_use_id] = block

    spans = []
    for tool_id, tool_use in tool_uses.items():
        span = _create_execute_tool_span(
            tool_use, tool_results.get(tool_id), integration
        )
        span.finish()
        spans.append(span)
    return spans


def _wrap_query(original_func: "Any") -> "Any":
    @wraps(original_func)
    async def wrapper(
        *, prompt: str, options: "Optional[Any]" = None, **kwargs: "Any"
    ) -> "AsyncGenerator[Any, None]":
        integration = sentry_sdk.get_client().get_integration(ClaudeAgentSDKIntegration)
        if integration is None:
            async for message in original_func(
                prompt=prompt, options=options, **kwargs
            ):
                yield message
            return

        model = getattr(options, "model", "") if options else ""
        invoke_span = _start_invoke_agent_span(prompt, options, integration)

        chat_span = get_start_span_function()(
            op=OP.GEN_AI_CHAT,
            name=f"claude-agent-sdk query {model}".strip(),
            origin=ClaudeAgentSDKIntegration.origin,
        )
        chat_span.__enter__()

        with capture_internal_exceptions():
            _set_span_input_data(chat_span, prompt, options, integration)

        collected_messages = []
        try:
            async for message in original_func(
                prompt=prompt, options=options, **kwargs
            ):
                collected_messages.append(message)
                yield message
        except Exception as exc:
            _capture_exception(exc)
            raise
        finally:
            with capture_internal_exceptions():
                _set_span_output_data(chat_span, collected_messages, integration)
            chat_span.__exit__(None, None, None)

            with capture_internal_exceptions():
                _process_tool_executions(collected_messages, integration)

            with capture_internal_exceptions():
                _end_invoke_agent_span(invoke_span, collected_messages, integration)

    return wrapper


def _wrap_client_query(original_method: "Any") -> "Any":
    @wraps(original_method)
    async def wrapper(self: "Any", prompt: str, **kwargs: "Any") -> "Any":
        integration = sentry_sdk.get_client().get_integration(ClaudeAgentSDKIntegration)
        if integration is None:
            return await original_method(self, prompt, **kwargs)

        options = getattr(self, "_options", None)
        model = getattr(options, "model", "") if options else ""

        invoke_span = _start_invoke_agent_span(prompt, options, integration)

        chat_span = get_start_span_function()(
            op=OP.GEN_AI_CHAT,
            name=f"claude-agent-sdk client {model}".strip(),
            origin=ClaudeAgentSDKIntegration.origin,
        )
        chat_span.__enter__()

        with capture_internal_exceptions():
            _set_span_input_data(chat_span, prompt, options, integration)

        self._sentry_query_context = {
            "invoke_span": invoke_span,
            "chat_span": chat_span,
            "integration": integration,
            "messages": [],
        }

        try:
            return await original_method(self, prompt, **kwargs)
        except Exception as exc:
            _capture_exception(exc)
            messages = self._sentry_query_context.get("messages", [])
            with capture_internal_exceptions():
                _set_span_output_data(chat_span, messages, integration)
            chat_span.__exit__(None, None, None)
            with capture_internal_exceptions():
                _end_invoke_agent_span(invoke_span, messages, integration)
            self._sentry_query_context = {}
            raise

    return wrapper


def _wrap_receive_response(original_method: "Any") -> "Any":
    @wraps(original_method)
    async def wrapper(self: "Any", **kwargs: "Any") -> "AsyncGenerator[Any, None]":
        integration = sentry_sdk.get_client().get_integration(ClaudeAgentSDKIntegration)
        if integration is None:
            async for message in original_method(self, **kwargs):
                yield message
            return

        context = getattr(self, "_sentry_query_context", {})
        invoke_span = context.get("invoke_span")
        chat_span = context.get("chat_span")
        stored_integration = context.get("integration", integration)
        messages = context.get("messages", [])

        try:
            async for message in original_method(self, **kwargs):
                messages.append(message)
                yield message
        except Exception as exc:
            _capture_exception(exc)
            raise
        finally:
            if chat_span is not None:
                with capture_internal_exceptions():
                    _set_span_output_data(chat_span, messages, stored_integration)
                chat_span.__exit__(None, None, None)

            with capture_internal_exceptions():
                _process_tool_executions(messages, stored_integration)

            if invoke_span is not None:
                with capture_internal_exceptions():
                    _end_invoke_agent_span(invoke_span, messages, stored_integration)

            self._sentry_query_context = {}

    return wrapper

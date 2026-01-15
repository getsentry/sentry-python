"""
Sentry integration for Claude Agent SDK (claude-agent-sdk).

This integration instruments the Claude Agent SDK to capture AI-related
telemetry data, including prompts, responses, token usage, and cost information.

The integration supports:
- query() function for one-shot queries
- ClaudeSDKClient for interactive sessions

Usage:
    import sentry_sdk
    from sentry_sdk.integrations.claude_code_sdk import ClaudeCodeSDKIntegration

    sentry_sdk.init(
        dsn="...",
        integrations=[ClaudeCodeSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,  # Required to capture prompts/responses
    )
"""

from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import (
    set_data_normalized,
    get_start_span_function,
)
from sentry_sdk.consts import OP, SPANDATA
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
    )
except ImportError:
    raise DidNotEnable("claude-agent-sdk not installed")

if TYPE_CHECKING:
    from typing import Any, AsyncGenerator, Optional
    from sentry_sdk.tracing import Span


class ClaudeCodeSDKIntegration(Integration):
    """
    Integration for Claude Agent SDK.

    Args:
        include_prompts: Whether to include prompts and responses in span data.
            Requires send_default_pii=True in Sentry init. Defaults to True.
    """

    identifier = "claude_code_sdk"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts: bool = True) -> None:
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once() -> None:
        version = package_version("claude_agent_sdk")
        _check_minimum_version(ClaudeCodeSDKIntegration, version)

        # Patch the query function
        claude_agent_sdk.query = _wrap_query(original_query)

        # Patch ClaudeSDKClient methods
        ClaudeSDKClient._original_query = ClaudeSDKClient.query
        ClaudeSDKClient.query = _wrap_client_query(ClaudeSDKClient.query)

        ClaudeSDKClient._original_receive_response = ClaudeSDKClient.receive_response
        ClaudeSDKClient.receive_response = _wrap_receive_response(
            ClaudeSDKClient.receive_response
        )


def _capture_exception(exc: "Any") -> None:
    """Capture an exception and set the current span as errored."""
    set_span_errored()

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "claude_code_sdk", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _set_span_input_data(
    span: "Span",
    prompt: "str",
    options: "Optional[Any]",
    integration: "ClaudeCodeSDKIntegration",
) -> None:
    """Set input data on the span."""
    set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, "claude-code")
    set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "chat")

    # Extract configuration from options if available
    if options is not None:
        # gen_ai.request.model (required) - will be set from response if not in options
        if hasattr(options, "model") and options.model:
            set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, options.model)

        # gen_ai.request.available_tools (optional)
        if hasattr(options, "allowed_tools") and options.allowed_tools:
            tools_list = [{"name": tool} for tool in options.allowed_tools]
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, tools_list, unpack=False
            )

        # gen_ai.request.messages (optional, requires PII)
        if hasattr(options, "system_prompt") and options.system_prompt:
            if should_send_default_pii() and integration.include_prompts:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_MESSAGES,
                    [
                        {"role": "system", "content": options.system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    unpack=False,
                )
        elif should_send_default_pii() and integration.include_prompts:
            set_data_normalized(
                span,
                SPANDATA.GEN_AI_REQUEST_MESSAGES,
                [{"role": "user", "content": prompt}],
                unpack=False,
            )
    elif should_send_default_pii() and integration.include_prompts:
        set_data_normalized(
            span,
            SPANDATA.GEN_AI_REQUEST_MESSAGES,
            [{"role": "user", "content": prompt}],
            unpack=False,
        )


def _extract_text_from_message(message: "Any") -> "Optional[str]":
    """Extract text content from an AssistantMessage."""
    if not isinstance(message, AssistantMessage):
        return None

    text_parts = []
    if hasattr(message, "content"):
        for block in message.content:
            if isinstance(block, TextBlock) and hasattr(block, "text"):
                text_parts.append(block.text)

    return "".join(text_parts) if text_parts else None


def _extract_tool_calls(message: "Any") -> "Optional[list]":
    """Extract tool calls from an AssistantMessage."""
    if not isinstance(message, AssistantMessage):
        return None

    tool_calls = []
    if hasattr(message, "content"):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tool_call = {"name": getattr(block, "name", "unknown")}
                if hasattr(block, "input"):
                    tool_call["input"] = block.input
                tool_calls.append(tool_call)

    return tool_calls if tool_calls else None


def _set_span_output_data(
    span: "Span",
    messages: "list",
    integration: "ClaudeCodeSDKIntegration",
) -> None:
    """Set output data on the span from collected messages."""
    response_texts = []
    tool_calls = []
    total_cost = None
    input_tokens = None
    output_tokens = None
    cached_input_tokens = None
    response_model = None

    for message in messages:
        if isinstance(message, AssistantMessage):
            text = _extract_text_from_message(message)
            if text:
                response_texts.append(text)

            calls = _extract_tool_calls(message)
            if calls:
                tool_calls.extend(calls)

            # Extract model from AssistantMessage
            if hasattr(message, "model") and message.model and not response_model:
                response_model = message.model

        elif isinstance(message, ResultMessage):
            if hasattr(message, "total_cost_usd"):
                total_cost = message.total_cost_usd
            if hasattr(message, "usage") and message.usage:
                usage = message.usage
                # Usage is a dict with keys like 'input_tokens', 'output_tokens'
                if isinstance(usage, dict):
                    if "input_tokens" in usage:
                        input_tokens = usage["input_tokens"]
                    if "output_tokens" in usage:
                        output_tokens = usage["output_tokens"]
                    # gen_ai.usage.input_tokens.cached (optional)
                    if "cache_read_input_tokens" in usage:
                        cached_input_tokens = usage["cache_read_input_tokens"]

    # gen_ai.response.model (optional, but use to fulfill required gen_ai.request.model)
    if response_model:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_MODEL, response_model)
        # Also set request model if not already set (gen_ai.request.model is required)
        # Access span's internal _data dict to check
        span_data = getattr(span, "_data", {})
        if SPANDATA.GEN_AI_REQUEST_MODEL not in span_data:
            set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, response_model)

    # gen_ai.response.text (optional, requires PII)
    if response_texts and should_send_default_pii() and integration.include_prompts:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, response_texts)

    # gen_ai.response.tool_calls (optional, requires PII)
    if tool_calls and should_send_default_pii() and integration.include_prompts:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, tool_calls, unpack=False
        )

    # Set token usage if available
    # gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.usage.total_tokens (optional)
    if input_tokens is not None or output_tokens is not None:
        record_token_usage(
            span,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    # gen_ai.usage.input_tokens.cached (optional)
    if cached_input_tokens is not None:
        set_data_normalized(
            span, SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cached_input_tokens
        )

    # Store cost information in span data
    if total_cost is not None:
        span.set_data("claude_code.total_cost_usd", total_cost)


def _wrap_query(original_func: "Any") -> "Any":
    """Wrap the query() async generator function."""

    @wraps(original_func)
    async def wrapper(
        *, prompt: str, options: "Optional[Any]" = None, **kwargs: "Any"
    ) -> "AsyncGenerator[Any, None]":
        integration = sentry_sdk.get_client().get_integration(ClaudeCodeSDKIntegration)
        if integration is None:
            async for message in original_func(prompt=prompt, options=options, **kwargs):
                yield message
            return

        model = ""
        if options is not None and hasattr(options, "model") and options.model:
            model = options.model

        span = get_start_span_function()(
            op=OP.GEN_AI_CHAT,
            name=f"claude-code query {model}".strip(),
            origin=ClaudeCodeSDKIntegration.origin,
        )
        span.__enter__()

        with capture_internal_exceptions():
            _set_span_input_data(span, prompt, options, integration)

        collected_messages = []

        try:
            async for message in original_func(prompt=prompt, options=options, **kwargs):
                collected_messages.append(message)
                yield message
        except Exception as exc:
            _capture_exception(exc)
            raise
        finally:
            with capture_internal_exceptions():
                _set_span_output_data(span, collected_messages, integration)
            span.__exit__(None, None, None)

    return wrapper


def _wrap_client_query(original_method: "Any") -> "Any":
    """Wrap the ClaudeSDKClient.query() method."""

    @wraps(original_method)
    async def wrapper(self: "Any", prompt: str, **kwargs: "Any") -> "Any":
        integration = sentry_sdk.get_client().get_integration(ClaudeCodeSDKIntegration)
        if integration is None:
            return await original_method(self, prompt, **kwargs)

        # Store query context on the client for use in receive_response
        if not hasattr(self, "_sentry_query_context"):
            self._sentry_query_context = {}

        model = ""
        if hasattr(self, "_options") and self._options:
            if hasattr(self._options, "model") and self._options.model:
                model = self._options.model

        span = get_start_span_function()(
            op=OP.GEN_AI_CHAT,
            name=f"claude-code client {model}".strip(),
            origin=ClaudeCodeSDKIntegration.origin,
        )
        span.__enter__()

        with capture_internal_exceptions():
            options = getattr(self, "_options", None)
            _set_span_input_data(span, prompt, options, integration)

        self._sentry_query_context = {
            "span": span,
            "integration": integration,
            "messages": [],
        }

        try:
            result = await original_method(self, prompt, **kwargs)
            return result
        except Exception as exc:
            _capture_exception(exc)
            # Close span on error
            with capture_internal_exceptions():
                _set_span_output_data(
                    span, self._sentry_query_context.get("messages", []), integration
                )
            span.__exit__(None, None, None)
            self._sentry_query_context = {}
            raise

    return wrapper


def _wrap_receive_response(original_method: "Any") -> "Any":
    """Wrap the ClaudeSDKClient.receive_response() method."""

    @wraps(original_method)
    async def wrapper(self: "Any", **kwargs: "Any") -> "AsyncGenerator[Any, None]":
        integration = sentry_sdk.get_client().get_integration(ClaudeCodeSDKIntegration)
        if integration is None:
            async for message in original_method(self, **kwargs):
                yield message
            return

        context = getattr(self, "_sentry_query_context", {})
        span = context.get("span")
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
            if span is not None:
                with capture_internal_exceptions():
                    _set_span_output_data(span, messages, stored_integration)
                span.__exit__(None, None, None)
                self._sentry_query_context = {}

    return wrapper

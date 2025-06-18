from functools import wraps
import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import event_from_exception

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from agents import Usage

try:
    import agents

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "openai_agents", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _get_start_span_function():
    # type: () -> Callable[..., Any]
    current_span = sentry_sdk.get_current_span()
    is_transaction = (
        current_span is not None and current_span.containing_transaction == current_span
    )
    return sentry_sdk.start_span if is_transaction else sentry_sdk.start_transaction


def _set_agent_data(span, agent):
    # type: (sentry_sdk.tracing.Span, agents.Agent) -> None
    span.set_data(
        SPANDATA.GEN_AI_SYSTEM, "openai"
    )  # See footnote for  https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/#gen-ai-system for explanation why.

    span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent.name)

    if agent.model_settings.max_tokens:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, agent.model_settings.max_tokens
        )

    if agent.model:
        span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, agent.model)

    if agent.model_settings.presence_penalty:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
            agent.model_settings.presence_penalty,
        )

    if agent.model_settings.temperature:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE, agent.model_settings.temperature
        )

    if agent.model_settings.top_p:
        span.set_data(SPANDATA.GEN_AI_REQUEST_TOP_P, agent.model_settings.top_p)

    if agent.model_settings.frequency_penalty:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
            agent.model_settings.frequency_penalty,
        )

    if len(agent.tools) > 0:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
            [vars(tool) for tool in agent.tools],
        )


def _set_usage_data(span, usage):
    # type: (sentry_sdk.tracing.Span, Usage) -> None
    span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens)
    span.set_data(
        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
        usage.input_tokens_details.cached_tokens,
    )
    span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens)
    span.set_data(
        SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
        usage.output_tokens_details.reasoning_tokens,
    )
    span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_tokens)


def _set_input_data(span, get_response_kwargs):
    # type: (sentry_sdk.tracing.Span, dict[str, Any]) -> None
    if not should_send_default_pii():
        return

    messages_by_role = {
        "system": [],
        "user": [],
        "assistant": [],
        "tool": [],
    }  # type: (dict[str, list[Any]])
    system_instructions = get_response_kwargs.get("system_instructions")
    if system_instructions:
        messages_by_role["system"].append({"type": "text", "text": system_instructions})

    for message in get_response_kwargs.get("input", []):
        if "role" in message:
            messages_by_role[message.get("role")].append(
                {"type": "text", "text": message.get("content")}
            )
        else:
            if message.get("type") == "function_call":
                messages_by_role["assistant"].append(message)
            elif message.get("type") == "function_call_output":
                messages_by_role["tool"].append(message)

    request_messages = []
    for role, messages in messages_by_role.items():
        if len(messages) > 0:
            request_messages.append({"role": role, "content": messages})

    span.set_data(SPANDATA.GEN_AI_REQUEST_MESSAGES, request_messages)


def _set_output_data(span, result):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    if not should_send_default_pii():
        return

    output_messages = {
        "response": [],
        "tool": [],
    }  # type: (dict[str, list[Any]])

    for output in result.output:
        if output.type == "function_call":
            output_messages["tool"].append(output.dict())
        elif output.type == "message":
            for output_message in output.content:
                try:
                    output_messages["response"].append(output_message.text)
                except AttributeError:
                    # Unknown output message type, just return the json
                    output_messages["response"].append(output_message.to_json())

    if len(output_messages["tool"]) > 0:
        span.set_data(SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, output_messages["tool"])

    if len(output_messages["response"]) > 0:
        span.set_data(SPANDATA.GEN_AI_RESPONSE_TEXT, output_messages["response"])


def _create_hook_wrapper(original_hook, sentry_hook):
    # type: (Callable[..., Any], Callable[..., Any]) -> Callable[..., Any]
    @wraps(original_hook)
    async def async_wrapper(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        await sentry_hook(*args, **kwargs)
        return await original_hook(*args, **kwargs)

    return async_wrapper


def _wrap_hooks(hooks):
    # type: (agents.RunHooks) -> agents.RunHooks
    """
    Our integration uses RunHooks to create spans. This function will either
    enable our SentryRunHooks or if the users has given custom RunHooks wrap
    them so the Sentry hooks and the users hooks are both called
    """
    import ipdb

    ipdb.set_trace()
    from .run_hooks import SentryRunHooks

    sentry_hooks = SentryRunHooks()

    if hooks is None:
        return sentry_hooks

    wrapped_hooks = type("SentryWrappedHooks", (hooks.__class__,), {})

    # Wrap all methods from RunHooks
    for method_name in dir(agents.RunHooks):
        if method_name.startswith("on_"):
            original_method = getattr(hooks, method_name)
            # Only wrap if the method exists in SentryRunHooks
            try:
                sentry_method = getattr(sentry_hooks, method_name)
                setattr(
                    wrapped_hooks,
                    method_name,
                    _create_hook_wrapper(original_method, sentry_method),
                )
            except AttributeError:
                # If method doesn't exist in SentryRunHooks, just use the original method
                setattr(wrapped_hooks, method_name, original_method)

    return wrapped_hooks()

import asyncio
from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.utils import event_from_exception
from sentry_sdk.consts import SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from agents import Usage

try:
    import agents
    from agents import RunHooks

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


def _usage_to_str(usage):
    # type: (Usage) -> str
    return (
        f"{usage.requests} requests, "
        f"{usage.input_tokens} input tokens, "
        f"{usage.output_tokens} output tokens, "
        f"{usage.total_tokens} total tokens"
    )


def _get_start_span_function():
    # type: () -> Callable
    current_span = sentry_sdk.get_current_span()
    is_transaction = (
        current_span is not None and current_span.containing_transaction == current_span
    )
    return sentry_sdk.start_span if is_transaction else sentry_sdk.start_transaction


def _create_hook_wrapper(original_hook, sentry_hook):
    # type: (Callable, Callable) -> Callable
    @wraps(original_hook)
    async def async_wrapper(*args, **kwargs):
        await sentry_hook(*args, **kwargs)
        return await original_hook(*args, **kwargs)

    return async_wrapper


def _wrap_hooks(hooks):
    # type: (RunHooks) -> RunHooks
    """
    Our integration uses RunHooks to create spans. This function will either
    enable our SentryRunHooks or if the users has given custom RunHooks wrap
    them so the Sentry hooks and the users hooks are both called
    """
    from .run_hooks import SentryRunHooks

    sentry_hooks = SentryRunHooks()

    if hooks is None:
        return sentry_hooks

    wrapped_hooks = type("SentryWrappedHooks", (hooks.__class__,), {})

    # Wrap all methods from RunHooks
    for method_name in dir(RunHooks):
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


def _set_agent_data(agent):
    # type: (agents.Agent) -> None
    span = sentry_sdk.get_current_span()

    """
    Thats the Agent object:
    {'handoff_description': None,
    'handoffs': [],
    'hooks': None,
    'input_guardrails': [],
    'instructions': 'You are a helpful research assistant. Given a query, come up '
                    'with a set of web searches to perform to best answer the '
                    'query. Output between 5 and 20 terms to query for.',
    'mcp_config': {},
    'mcp_servers': [],
    'model': 'gpt-4o',
    'model_settings': ModelSettings(temperature=None,
                                    top_p=None,
                                    frequency_penalty=None,
                                    presence_penalty=None,
                                    tool_choice=None,
                                    parallel_tool_calls=None,
                                    truncation=None,
                                    max_tokens=None,
                                    reasoning=None,
                                    metadata=None,
                                    store=None,
                                    include_usage=None,
                                    extra_query=None,
                                    extra_body=None,
                                    extra_headers=None),
    'name': 'PlannerAgent',
    'output_guardrails': [],
    'output_type': <class '__main__.WebSearchPlan'>,
    'reset_tool_choice': True,
    'tool_use_behavior': 'run_llm_again',
    'tools': []}
    """
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


def _create_run_wrapper(original_func):
    # type: (Callable) -> Callable
    """
    Wraps the run methods of the Runner class to create a root span for the agent runs.
    """
    is_async = asyncio.iscoroutinefunction(original_func)

    @classmethod
    @wraps(original_func)
    async def async_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = args[0]
        with _get_start_span_function()(name=f"{agent.name} workflow"):
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            _set_agent_data(agent)

            result = await original_func(*args, **kwargs)

            return result

    @classmethod
    @wraps(original_func)
    def sync_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = args[0]
        with _get_start_span_function()(name=f"{agent.name} workflow"):
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            _set_agent_data(agent)

            result = original_func(*args, **kwargs)

            return result

    return async_wrapper if is_async else sync_wrapper


def _create_get_model_wrapper(original_get_model):
    """
    Wraps the get_response method of the model to create a AI client span.
    """
    from .spans import ai_client_span, finish_ai_client_span

    @classmethod
    @wraps(original_get_model)
    def wrapped_get_model(cls, agent, run_config):
        model = original_get_model(agent, run_config)
        original_get_response = model.get_response

        @wraps(original_get_response)
        async def wrapped_get_response(*args, **kwargs):
            with ai_client_span(agent, model, run_config, kwargs):
                result = await original_get_response(*args, **kwargs)
                finish_ai_client_span(agent, model, run_config, kwargs, result)
            return result

        model.get_response = wrapped_get_response
        return model

    return wrapped_get_model

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import event_from_exception
from functools import wraps
import asyncio

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable

try:
    import agents
    from agents import (
        Agent,
        RunContextWrapper,
        RunHooks,
        Tool,
        Usage,
    )

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": OpenAIAgentsIntegration.identifier, "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


class SentryRunHooks(RunHooks):
    def _usage_to_str(self, usage):
        # type: (Usage) -> str
        return f"{usage.requests} requests, {usage.input_tokens} input tokens, {usage.output_tokens} output tokens, {usage.total_tokens} total tokens"

    async def on_agent_start(self, context, agent):
        # type: (RunContextWrapper, Agent) -> None
        print(
            f"### Agent {agent.name} started. Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(op="gen_ai.agent_start", description=agent.name)
        span.__enter__()

    async def on_agent_end(self, context, agent, output):
        # type: (RunContextWrapper, Agent, Any) -> None
        print(
            f"### Agent '{agent.name}' ended with output {output}. Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            current_span.__exit__(None, None, None)

    async def on_tool_start(self, context, agent, tool):
        # type: (RunContextWrapper, Agent, Tool) -> None
        print(
            f"### Tool {tool.name} started. Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(op="gen_ai.tool_start", description=tool.name)
        span.__enter__()

    async def on_tool_end(self, context, agent, tool, result):
        # type: (RunContextWrapper, Agent, Tool, str) -> None
        print(
            f"### Tool {tool.name} ended with result {result}. Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            current_span.__exit__(None, None, None)

    async def on_handoff(self, context, from_agent, to_agent):
        # type: (RunContextWrapper, Agent, Agent) -> None
        print(
            f"### Handoff from '{from_agent.name}' to '{to_agent.name}'. Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            with current_span.start_child(
                op="gen_ai.handoff", description=f"{from_agent.name} > {to_agent.name}"
            ):
                pass
            current_span.__exit__(None, None, None)


def _get_span_function():
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
    Our integration uses RunHooks to create spans. This function will either enable our SentryRunHooks
    or if the users has given custom RunHooks wrap them so the Sentry hooks and the users hooks are both called
    """
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
        with _get_span_function()(name=agent.name):
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            result = await original_func(*args, **kwargs)
            return result

    @classmethod
    @wraps(original_func)
    def sync_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = args[0]
        with _get_span_function()(name=agent.name):
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            result = original_func(*args, **kwargs)
            return result

    return async_wrapper if is_async else sync_wrapper


def _patch_runner():
    # type: () -> None
    agents.Runner.run = _create_run_wrapper(agents.Runner.run)
    agents.Runner.run_sync = _create_run_wrapper(agents.Runner.run_sync)
    agents.Runner.run_streamed = _create_run_wrapper(agents.Runner.run_streamed)


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"
    origin = f"auto.ai.{identifier}"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_runner()

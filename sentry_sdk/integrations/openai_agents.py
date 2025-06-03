import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import event_from_exception
from functools import wraps
import asyncio

from typing import Any

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
    def _usage_to_str(self, usage: Usage) -> str:
        return f"{usage.requests} requests, {usage.input_tokens} input tokens, {usage.output_tokens} output tokens, {usage.total_tokens} total tokens"

    async def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        print(
            f"### Agent {agent.name} started. Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(op="gen_ai.agent_start", description=agent.name)
        span.__enter__()

    async def on_agent_end(
        self, context: RunContextWrapper, agent: Agent, output: Any
    ) -> None:
        print(
            f"### Agent '{agent.name}' ended with output {output}. Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            current_span.__exit__(None, None, None)

    async def on_tool_start(
        self, context: RunContextWrapper, agent: Agent, tool: Tool
    ) -> None:
        print(
            f"### Tool {tool.name} started. Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(op="gen_ai.tool_start", description=tool.name)
        span.__enter__()

    async def on_tool_end(
        self, context: RunContextWrapper, agent: Agent, tool: Tool, result: str
    ) -> None:
        print(
            f"### Tool {tool.name} ended with result {result}. Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            current_span.__exit__(None, None, None)

    async def on_handoff(
        self, context: RunContextWrapper, from_agent: Agent, to_agent: Agent
    ) -> None:
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
    current_span = sentry_sdk.get_current_span()
    is_transaction = (
        current_span is not None and current_span.containing_transaction == current_span
    )
    return sentry_sdk.start_span if is_transaction else sentry_sdk.start_transaction


def _create_wrapper(original_func):
    is_async = asyncio.iscoroutinefunction(original_func)

    @classmethod
    @wraps(original_func)
    async def async_wrapper(cls, *args, **kwargs):
        agent = args[0]
        with _get_span_function()(name=agent.name):
            result = await original_func(*args, **kwargs)
            return result

    @classmethod
    @wraps(original_func)
    def sync_wrapper(cls, *args, **kwargs):
        agent = args[0]
        with _get_span_function()(name=agent.name):
            result = original_func(*args, **kwargs)
            return result

    return async_wrapper if is_async else sync_wrapper


def _patch_runner():
    agents.Runner.run = _create_wrapper(agents.Runner.run)
    agents.Runner.run_sync = _create_wrapper(agents.Runner.run_sync)
    agents.Runner.run_streamed = _create_wrapper(agents.Runner.run_streamed)


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"
    origin = f"auto.ai.{identifier}"

    @staticmethod
    def setup_once():
        # type: () -> None
        _patch_runner()
        pass

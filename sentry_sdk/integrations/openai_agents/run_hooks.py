import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

try:
    from agents import (
        Agent,
        RunContextWrapper,
        RunHooks,
        Tool,
        Usage,
    )

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


class SentryRunHooks(RunHooks):
    def _usage_to_str(self, usage):
        # type: (Usage) -> str
        return (
            f"{usage.requests} requests, "
            f"{usage.input_tokens} input tokens, "
            f"{usage.output_tokens} output tokens, "
            f"{usage.total_tokens} total tokens"
        )

    async def on_agent_start(self, context, agent):
        # type: (RunContextWrapper, Agent) -> None
        print(
            f"### Agent {agent.name} started. "
            f"Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(
            op="gen_ai.invoke_agent", name=f"invoke_agent {agent.name}"
        )
        span.__enter__()

    async def on_agent_end(self, context, agent, output):
        # type: (RunContextWrapper, Agent, Any) -> None
        print(
            f"### Agent '{agent.name}' ended with output {output}. "
            f"Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            current_span.__exit__(None, None, None)

    async def on_tool_start(self, context, agent, tool):
        # type: (RunContextWrapper, Agent, Tool) -> None
        print(
            f"### Tool {tool.name} started. "
            f"Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(
            op="gen_ai.execute_tool", name=f"execute_tool {tool.name}"
        )
        span.__enter__()

    async def on_tool_end(self, context, agent, tool, result):
        # type: (RunContextWrapper, Agent, Tool, str) -> None
        print(
            f"### Tool {tool.name} ended with result {result}. "
            f"Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            current_span.__exit__(None, None, None)

    async def on_handoff(self, context, from_agent, to_agent):
        # type: (RunContextWrapper, Agent, Agent) -> None
        print(
            f"### Handoff from '{from_agent.name}' to '{to_agent.name}'. "
            f"Usage: {self._usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            with current_span.start_child(
                op="gen_ai.handoff",
                name=f"handoff from {from_agent.name} to {to_agent.name}",
            ):
                pass
            current_span.__exit__(None, None, None)

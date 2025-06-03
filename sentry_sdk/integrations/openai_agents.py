import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import event_from_exception

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


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": OpenAIAgentsIntegration.identifier, "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


class SentryRunHooks(RunHooks):
    def __init__(self):
        self.event_counter = 0

    def _usage_to_str(self, usage: Usage) -> str:
        return f"{usage.requests} requests, {usage.input_tokens} input tokens, {usage.output_tokens} output tokens, {usage.total_tokens} total tokens"

    async def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        self.event_counter += 1
        print(
            f"### {self.event_counter}: Agent {agent.name} started. Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(op="gen_ai.agent_start", description=agent.name)
        span.__enter__()
        self.agent_span = span

    async def on_agent_end(
        self, context: RunContextWrapper, agent: Agent, output: Any
    ) -> None:
        self.event_counter += 1
        print(
            f"### {self.event_counter}: Agent '{agent.name}' ended with output {output}. Usage: {self._usage_to_str(context.usage)}"
        )
        print(self.agent_span)
        if self.agent_span:
            print(f"Span exit agent: {self.agent_span}")
            self.agent_span.__exit__(None, None, None)
            self.agent_span = None

    async def on_tool_start(
        self, context: RunContextWrapper, agent: Agent, tool: Tool
    ) -> None:
        self.event_counter += 1
        print(
            f"### {self.event_counter}: Tool {tool.name} started. Usage: {self._usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(op="gen_ai.tool_start", description=tool.name)
        span.__enter__()
        self.tool_span = span

    async def on_tool_end(
        self, context: RunContextWrapper, agent: Agent, tool: Tool, result: str
    ) -> None:
        self.event_counter += 1
        print(
            f"### {self.event_counter}: Tool {tool.name} ended with result {result}. Usage: {self._usage_to_str(context.usage)}"
        )
        if self.tool_span:
            print(f"Span exit tool: {self.tool_span}")
            self.tool_span.__exit__(None, None, None)
            self.tool_span = None

    async def on_handoff(
        self, context: RunContextWrapper, from_agent: Agent, to_agent: Agent
    ) -> None:
        self.event_counter += 1
        print(
            f"### {self.event_counter}: Handoff from '{from_agent.name}' to '{to_agent.name}'. Usage: {self._usage_to_str(context.usage)}"
        )
        if self.agent_span:
            span = self.agent_span.start_child(
                op="gen_ai.handoff", description=f"{from_agent.name} -> {to_agent.name}"
            )
            print(f"Span enter handoff: {span}")
            span.__enter__()
            print(f"Span exit handoff: {span}")
            span.__exit__(None, None, None)

            print(f"Span exit agent: {self.agent_span}")
            self.agent_span.__exit__(None, None, None)
            self.agent_span = None


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"
    origin = f"auto.ai.{identifier}"

    # def __init__(self):
    #     pass

    @staticmethod
    def setup_once():
        # type: () -> None
        pass

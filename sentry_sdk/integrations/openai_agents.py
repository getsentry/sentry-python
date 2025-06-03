import sys

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import event_from_exception

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
    from agents.tracing.setup import TraceProvider
    from agents.tracing.spans import Span, TSpanData
    from agents.tracing.traces import Trace

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


class SentryTraceProvider:
    def __init__(self, original: TraceProvider):
        self.original = original

    def create_trace(
        self,
        name: str,
        trace_id: str | None = None,
        disabled: bool = False,
        **kwargs: Any,
    ) -> Trace:
        print(f"[SentryTraceProvider] create_trace: {name}")
        trace = self.original.create_trace(
            name, trace_id=trace_id, disabled=disabled, **kwargs
        )
        return trace

    def create_span(
        self,
        span_data: TSpanData,
        span_id: str | None = None,
        parent: Trace | Span[Any] | None = None,
        disabled: bool = False,
    ) -> Span[TSpanData]:
        print(f"[SentryTraceProvider] create_span: {span_data}")
        span = self.original.create_span(span_data, span_id, parent, disabled)

        # current_span = Scope.get_current_span()
        # current_trace = Scope.get_current_trace()

        # sentry_span = sentry_sdk.start_span(
        #     op=AGENTS_TO_OP[span_data.__class__.__name__],
        #     name=AGENTS_TO_NAME[span_data.__class__.__name__],
        #     attributes=span_data.export()
        # )
        # sentry_span.finish()
        return span

    def __getattr__(self, item: Any) -> Any:
        return getattr(self.original, item)


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


def _patch_tracer_provider():
    # Monkey path trace provider of openai-agents
    name = "GLOBAL_TRACE_PROVIDER"
    original = getattr(agents.tracing, name)
    already_wrapped = isinstance(original, SentryTraceProvider)
    if not already_wrapped:
        wrapper = SentryTraceProvider(original)
        for module_name, mod in sys.modules.items():
            if module_name.startswith("agents"):
                try:
                    if getattr(mod, name, None) is original:
                        setattr(mod, name, wrapper)
                except Exception:  # pragma: no cover
                    pass


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"
    origin = f"auto.ai.{identifier}"

    # def __init__(self):
    #     pass

    @staticmethod
    def setup_once():
        # type: () -> None
        # _patch_tracer_provider()

        pass

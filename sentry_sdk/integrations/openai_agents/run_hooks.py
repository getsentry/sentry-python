from sentry_sdk.integrations import DidNotEnable

from typing import TYPE_CHECKING

from sentry_sdk.integrations.openai_agents.spans import (
    execute_tool_span,
    handoff_span,
    invoke_agent_span,
    finish_execute_tool_span,
    finish_invoke_agent_span,
)


if TYPE_CHECKING:
    from typing import Any

try:
    from agents import (
        Agent,
        RunContextWrapper,
        RunHooks,
        Tool,
    )

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


class SentryRunHooks(RunHooks):
    async def on_agent_start(self, context, agent) -> None:
        # type: (RunContextWrapper, Agent) -> None
        invoke_agent_span(context, agent)

    async def on_agent_end(self, context, agent, output):
        # type: (RunContextWrapper, Agent, Any) -> None
        finish_invoke_agent_span(context, agent, output)

    async def on_tool_start(self, context, agent, tool):
        # type: (RunContextWrapper, Agent, Tool) -> None
        execute_tool_span(context, agent, tool)

    async def on_tool_end(self, context, agent, tool, result):
        # type: (RunContextWrapper, Agent, Tool, str) -> None
        finish_execute_tool_span(context, agent, tool, result)

    async def on_handoff(
        self, context: RunContextWrapper, from_agent: Agent, to_agent: Agent
    ) -> None:
        handoff_span(context, from_agent, to_agent)

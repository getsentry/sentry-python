import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from typing import TYPE_CHECKING

from sentry_sdk.integrations.openai_agents.spans import (
    execute_tool_span,
    invoke_agent_span,
    handoff_span,
)

from .utils import _usage_to_str


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
    async def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        invoke_agent_span(context, agent)

    async def on_agent_end(self, context, agent, output):
        # type: (RunContextWrapper, Agent, Any) -> None
        print(
            f"### Agent '{agent.name}' ended with output {output}. "
            f"Usage: {_usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            current_span.__exit__(None, None, None)

    async def on_tool_start(self, context, agent, tool):
        # type: (RunContextWrapper, Agent, Tool) -> None
        execute_tool_span(context, agent, tool)

    async def on_tool_end(self, context, agent, tool, result):
        # type: (RunContextWrapper, Agent, Tool, str) -> None
        print(
            f"### Tool {tool.name} ended with result {result}. "
            f"Usage: {_usage_to_str(context.usage)}"
        )
        current_span = sentry_sdk.get_current_span()
        if current_span:
            import ipdb

            ipdb.set_trace()
            current_span.__exit__(None, None, None)

    async def on_handoff(
        self, context: RunContextWrapper, from_agent: Agent, to_agent: Agent
    ) -> None:
        handoff_span(context, from_agent, to_agent)

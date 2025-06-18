from sentry_sdk.integrations import DidNotEnable

from .spans import handoff_span, invoke_agent_span, update_invoke_agent_span

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


try:
    import agents
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


class SentryRunHooks(agents.RunHooks):  # type: ignore[misc]
    async def on_agent_start(self, context, agent):
        # type: (agents.RunContextWrapper, agents.Agent) -> None
        invoke_agent_span(context, agent)

    async def on_agent_end(self, context, agent, output):
        # type: (agents.RunContextWrapper, agents.Agent, Any) -> None
        update_invoke_agent_span(context, agent, output)

    async def on_handoff(
        self,
        context: agents.RunContextWrapper,
        from_agent: agents.Agent,
        to_agent: agents.Agent,
    ) -> None:
        handoff_span(context, from_agent, to_agent)

from sentry_sdk.integrations import DidNotEnable

from .spans import handoff_span


try:
    import agents
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


class SentryRunHooks(agents.RunHooks):  # type: ignore[misc]
    async def on_handoff(
        self,
        context: agents.RunContextWrapper,
        from_agent: agents.Agent,
        to_agent: agents.Agent,
    ) -> None:
        handoff_span(context, from_agent, to_agent)

import sentry_sdk
from sentry_sdk.integrations.openai_agents.utils import _usage_to_str

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent, RunContextWrapper


def handoff_span(context, from_agent, to_agent):
    # type: (RunContextWrapper, Agent, Agent) -> None
    print(
        f"### Handoff from '{from_agent.name}' to '{to_agent.name}'. "
        f"Usage: {_usage_to_str(context.usage)}"
    )
    current_span = sentry_sdk.get_current_span()
    if current_span:
        with current_span.start_child(
            op="gen_ai.handoff",
            name=f"handoff from {from_agent.name} to {to_agent.name}",
        ) as span:
            span.set_data("gen_ai.operation.name", "handoff")

        current_span.__exit__(None, None, None)

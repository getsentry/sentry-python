import sentry_sdk
from sentry_sdk.integrations.openai_agents.utils import _usage_to_str, _set_agent_data

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from agents import Agent, RunContextWrapper


def invoke_agent_span(context, agent):
    # type: (RunContextWrapper, Agent) -> None
    print(f"### Agent {agent.name} started. " f"Usage: {_usage_to_str(context.usage)}")
    span = sentry_sdk.start_span(
        op="gen_ai.invoke_agent", name=f"invoke_agent {agent.name}"
    )
    span.__enter__()

    span.set_data("gen_ai.operation.name", "invoke_agent")

    _set_agent_data(agent)


def finish_invoke_agent_span(context, agent, output):
    # type: (RunContextWrapper, Agent, Any) -> None
    print(
        f"### Agent '{agent.name}' ended with output {output}. "
        f"Usage: {_usage_to_str(context.usage)}"
    )
    current_span = sentry_sdk.get_current_span()
    if current_span:
        current_span.__exit__(None, None, None)

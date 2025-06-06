import sentry_sdk
from sentry_sdk.integrations.openai_agents.utils import _usage_to_str, _set_agent_data
from sentry_sdk.consts import OP, SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import agents
    from typing import Any


def invoke_agent_span(context, agent):
    # type: (agents.RunContextWrapper, agents.Agent) -> None
    print(f"### Agent {agent.name} started. " f"Usage: {_usage_to_str(context.usage)}")
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_INVOKE_AGENT, name=f"invoke_agent {agent.name}"
    )
    span.__enter__()

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    _set_agent_data(agent)


def update_invoke_agent_span(context, agent, output):
    # type: (agents.RunContextWrapper, agents.Agent, Any) -> None
    print(
        f"### Agent '{agent.name}' ended with output {output}. "
        f"Usage: {_usage_to_str(context.usage)}"
    )
    current_span = sentry_sdk.get_current_span()
    if current_span:
        current_span.__exit__(None, None, None)

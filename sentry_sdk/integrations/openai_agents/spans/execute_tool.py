import sentry_sdk
from sentry_sdk.integrations.openai_agents.utils import _set_agent_data, _usage_to_str

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent, RunContextWrapper, Tool


def execute_tool_span(context, agent, tool):
    # type: (RunContextWrapper, Agent, Tool) -> None

    print(f"### Tool {tool.name} started. " f"Usage: {_usage_to_str(context.usage)}")
    span = sentry_sdk.start_span(
        op="gen_ai.execute_tool", name=f"execute_tool {tool.name}"
    )
    span.__enter__()

    span.set_data("gen_ai.operation.name", "execute_tool")
    span.set_data("gen_ai.tool.name", tool.name)
    span.set_data("gen_ai.tool.description", tool.description)

    if tool.__class__.__name__ == "FunctionTool":
        span.set_data("gen_ai.tool.type", "function")

    _set_agent_data(agent)


def finish_execute_tool_span(context, agent, tool, result):
    # type: (RunContextWrapper, Agent, Tool, str) -> None
    print(
        f"### Tool {tool.name} ended with result {result}. "
        f"Usage: {_usage_to_str(context.usage)}"
    )
    current_span = sentry_sdk.get_current_span()
    if current_span:
        current_span.__exit__(None, None, None)

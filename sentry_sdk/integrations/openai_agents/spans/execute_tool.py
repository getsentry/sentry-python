import sentry_sdk
from sentry_sdk.integrations.openai_agents.utils import _set_agent_data, _usage_to_str
from sentry_sdk.consts import OP, SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent, RunContextWrapper, Tool


def execute_tool_span(context, agent, tool):
    # type: (RunContextWrapper, Agent, Tool) -> None
    print(f"### Tool {tool.name} started. " f"Usage: {_usage_to_str(context.usage)}")
    span = sentry_sdk.start_span(
        op=OP.GEN_AI_EXECUTE_TOOL, name=f"execute_tool {tool.name}"
    )
    span.__enter__()

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
    span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool.name)
    span.set_data(SPANDATA.GEN_AI_TOOL_DESCRIPTION, tool.description)

    if tool.__class__.__name__ == "FunctionTool":
        span.set_data(SPANDATA.GEN_AI_TOOL_TYPE, "function")

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

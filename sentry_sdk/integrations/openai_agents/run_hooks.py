import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from typing import TYPE_CHECKING

from .utils import _set_agent_data


if TYPE_CHECKING:
    from typing import Any

try:
    from agents import (
        Agent,
        RunContextWrapper,
        RunHooks,
        Tool,
        Usage,
    )

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _usage_to_str(usage):
    # type: (Usage) -> str
    return (
        f"{usage.requests} requests, "
        f"{usage.input_tokens} input tokens, "
        f"{usage.output_tokens} output tokens, "
        f"{usage.total_tokens} total tokens"
    )


def execute_tool_span(context, agent, tool):
    print(f"### Tool {tool.name} started. " f"Usage: {_usage_to_str(context.usage)}")
    span = sentry_sdk.start_span(
        op="gen_ai.execute_tool", name=f"execute_tool {tool.name}"
    )
    span.__enter__()
    import ipdb

    ipdb.set_trace()
    span.set_data("gen_ai.operation.name", "execute_tool")

    if tool.__class__.__name__ == "FunctionTool":
        span.set_data("gen_ai.tool.type", "function")

    span.set_data("gen_ai.tool.name", tool.name)
    span.set_data("gen_ai.tool.description", tool.description)
    # span.set_data("gen_ai.tool.message", )
    _set_agent_data(agent)


class SentryRunHooks(RunHooks):
    async def on_agent_start(self, context, agent):
        # type: (RunContextWrapper, Agent) -> None
        print(
            f"### Agent {agent.name} started. " f"Usage: {_usage_to_str(context.usage)}"
        )
        span = sentry_sdk.start_span(
            op="gen_ai.invoke_agent", name=f"invoke_agent {agent.name}"
        )
        span.__enter__()
        _set_agent_data(agent)

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

    async def on_handoff(self, context, from_agent, to_agent):
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
            ):
                pass
            current_span.__exit__(None, None, None)

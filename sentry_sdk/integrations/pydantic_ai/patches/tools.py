from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import execute_tool_span, update_execute_tool_span

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable

try:
    import pydantic_ai
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


def _patch_tool_execution():
    # type: () -> None
    """
    Patches tool execution to create execute_tool spans.

    In pydantic-ai, tools are managed through the ToolManager.
    We patch the toolset.call_tool method to create spans around tool execution.

    Note: pydantic-ai has built-in OpenTelemetry instrumentation for tools.
    Our patching adds Sentry-specific span data on top of that.
    """
    from pydantic_ai.toolsets.abstract import AbstractToolset
    from pydantic_ai.toolsets.function import FunctionToolset

    def create_wrapped_call_tool(original_call_tool):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        """Create a wrapped call_tool method."""

        @wraps(original_call_tool)
        async def wrapped_call_tool(self, name, args_dict, ctx, tool):
            # type: (Any, str, Any, Any, Any) -> Any
            # Always create span if we're in a Sentry transaction context
            import sentry_sdk

            current_span = sentry_sdk.get_current_span()
            should_create_span = current_span is not None

            if should_create_span:
                # Get agent from Sentry scope
                agent_data = (
                    sentry_sdk.get_current_scope()._contexts.get("pydantic_ai_agent")
                    or {}
                )
                agent = agent_data.get("_agent")

                with execute_tool_span(name, args_dict, agent) as span:
                    result = await original_call_tool(self, name, args_dict, ctx, tool)
                    update_execute_tool_span(span, result)
                    return result
            else:
                result = await original_call_tool(self, name, args_dict, ctx, tool)
                return result

        return wrapped_call_tool

    # Patch AbstractToolset's call_tool method
    AbstractToolset.call_tool = create_wrapped_call_tool(AbstractToolset.call_tool)  # type: ignore

    # Also patch FunctionToolset specifically since it overrides call_tool
    FunctionToolset.call_tool = create_wrapped_call_tool(FunctionToolset.call_tool)  # type: ignore

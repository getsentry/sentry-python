from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import (
    invoke_agent_span,
    update_invoke_agent_span,
    ai_client_span,
    update_ai_client_span,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable

try:
    import pydantic_ai
    from pydantic_ai._agent_graph import UserPromptNode, ModelRequestNode, CallToolsNode
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


def _patch_graph_nodes():
    # type: () -> None
    """
    Patches the graph node execution to create appropriate spans.

    UserPromptNode -> Creates invoke_agent span
    ModelRequestNode -> Creates ai_client span for model requests
    CallToolsNode -> Handles tool calls (spans created in tool patching)
    """

    # Patch UserPromptNode to create invoke_agent spans
    original_user_prompt_run = UserPromptNode.run

    @wraps(original_user_prompt_run)
    async def wrapped_user_prompt_run(self, ctx):
        # type: (Any, Any) -> Any
        # Extract data from context
        user_prompt = getattr(self, "user_prompt", None)
        model = None
        model_settings = None

        if hasattr(ctx, "deps"):
            model = getattr(ctx.deps, "model", None)
            model_settings = getattr(ctx.deps, "model_settings", None)

        # Try to get agent from Sentry scope
        import sentry_sdk

        agent_data = (
            sentry_sdk.get_current_scope()._contexts.get("pydantic_ai_agent") or {}
        )
        agent = agent_data.get("_agent")

        # Create and store the invoke_agent span
        span = invoke_agent_span(user_prompt, agent, model, model_settings)
        # Store span in context for later use
        if hasattr(ctx, "state"):
            ctx.state._sentry_invoke_span = span  # type: ignore

        result = await original_user_prompt_run(self, ctx)
        return result

    UserPromptNode.run = wrapped_user_prompt_run  # type: ignore

    # Patch ModelRequestNode to create ai_client spans
    original_model_request_run = ModelRequestNode.run

    @wraps(original_model_request_run)
    async def wrapped_model_request_run(self, ctx):
        # type: (Any, Any) -> Any
        # Extract data from context
        model = None
        model_settings = None
        if hasattr(ctx, "deps"):
            model = getattr(ctx.deps, "model", None)
            model_settings = getattr(ctx.deps, "model_settings", None)

        model_request = getattr(self, "request", None)

        with ai_client_span(model_request, None, model, model_settings) as span:
            result = await original_model_request_run(self, ctx)

            # Extract response from result if available
            model_response = None
            if hasattr(result, "model_response"):
                model_response = result.model_response

            update_ai_client_span(span, model_response)
            return result

    ModelRequestNode.run = wrapped_model_request_run  # type: ignore

    # Patch CallToolsNode to close invoke_agent span when done
    original_call_tools_run = CallToolsNode.run

    @wraps(original_call_tools_run)
    async def wrapped_call_tools_run(self, ctx):
        # type: (Any, Any) -> Any
        result = await original_call_tools_run(self, ctx)

        # Check if this is an End node (final result)
        from pydantic_graph import End

        if isinstance(result, End):
            # Close the invoke_agent span if it exists
            if hasattr(ctx, "state") and hasattr(ctx.state, "_sentry_invoke_span"):
                span = ctx.state._sentry_invoke_span
                output = None
                if hasattr(result, "data") and hasattr(result.data, "output"):
                    output = result.data.output
                update_invoke_agent_span(span, output)
                delattr(ctx.state, "_sentry_invoke_span")

        return result

    CallToolsNode.run = wrapped_call_tools_run  # type: ignore

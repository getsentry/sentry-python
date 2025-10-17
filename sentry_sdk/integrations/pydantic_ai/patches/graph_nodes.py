from functools import wraps

from ..spans import (
    invoke_agent_span,
    update_invoke_agent_span,
    ai_client_span,
    update_ai_client_span,
)
from pydantic_ai._agent_graph import UserPromptNode, ModelRequestNode, CallToolsNode

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


def _patch_graph_nodes():
    # type: () -> None
    """
    Patches the graph node execution to create appropriate spans.

    UserPromptNode -> Creates invoke_agent span
    ModelRequestNode -> Creates ai_client span for model requests
    CallToolsNode -> Handles tool calls (spans created in tool patching)
    """

    def _extract_span_data(node, ctx):
        # type: (Any, Any) -> tuple[list[Any], Any, Any]
        """Extract common data needed for creating chat spans.

        Returns:
            Tuple of (messages, model, model_settings)
        """
        # Extract model and settings from context
        model = None
        model_settings = None
        if hasattr(ctx, "deps"):
            model = getattr(ctx.deps, "model", None)
            model_settings = getattr(ctx.deps, "model_settings", None)

        # Build full message list: history + current request
        messages = []
        if hasattr(ctx, "state") and hasattr(ctx.state, "message_history"):
            messages.extend(ctx.state.message_history)

        current_request = getattr(node, "request", None)
        if current_request:
            messages.append(current_request)

        return messages, model, model_settings

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
            ctx.state._sentry_invoke_span = span

        result = await original_user_prompt_run(self, ctx)
        return result

    UserPromptNode.run = wrapped_user_prompt_run  # type: ignore[method-assign]

    # Patch ModelRequestNode to create ai_client spans
    original_model_request_run = ModelRequestNode.run

    @wraps(original_model_request_run)
    async def wrapped_model_request_run(self, ctx):
        # type: (Any, Any) -> Any
        messages, model, model_settings = _extract_span_data(self, ctx)

        with ai_client_span(messages, None, model, model_settings) as span:
            result = await original_model_request_run(self, ctx)

            # Extract response from result if available
            model_response = None
            if hasattr(result, "model_response"):
                model_response = result.model_response

            update_ai_client_span(span, model_response)
            return result

    ModelRequestNode.run = wrapped_model_request_run  # type: ignore

    # Patch ModelRequestNode.stream for streaming requests
    original_model_request_stream = ModelRequestNode.stream

    def create_wrapped_stream(original_stream_method):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        """Create a wrapper for ModelRequestNode.stream that creates chat spans."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        @wraps(original_stream_method)
        async def wrapped_model_request_stream(self, ctx):
            # type: (Any, Any) -> Any
            messages, model, model_settings = _extract_span_data(self, ctx)

            # Create chat span for streaming request
            import sentry_sdk

            span = ai_client_span(messages, None, model, model_settings)
            span.__enter__()

            try:
                # Call the original stream method
                async with original_stream_method(self, ctx) as stream:
                    yield stream

                # After streaming completes, update span with response data
                # The ModelRequestNode stores the final response in _result
                model_response = None
                if hasattr(self, "_result") and self._result is not None:
                    # _result is a NextNode containing the model_response
                    if hasattr(self._result, "model_response"):
                        model_response = self._result.model_response

                update_ai_client_span(span, model_response)
            finally:
                # Close the span after streaming completes
                span.__exit__(None, None, None)

        return wrapped_model_request_stream

    ModelRequestNode.stream = create_wrapped_stream(original_model_request_stream)  # type: ignore

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

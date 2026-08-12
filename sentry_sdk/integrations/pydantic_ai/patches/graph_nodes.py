from contextlib import asynccontextmanager
from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from .._extract import extract_graph_request_data
from ..spans import (
    ai_client_span,
    update_ai_client_span,
)

try:
    from pydantic_ai._agent_graph import ModelRequestNode
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Optional

    from pydantic_ai.messages import ModelResponse


def _patch_graph_nodes() -> None:
    """
    Patches the graph node execution to create appropriate spans.

    ModelRequestNode -> Creates ai_client span for model requests
    CallToolsNode -> Handles tool calls (spans created in tool patching)
    """

    # Patch ModelRequestNode to create ai_client spans
    original_model_request_run = ModelRequestNode.run

    @wraps(original_model_request_run)
    async def wrapped_model_request_run(self: "Any", ctx: "Any") -> "Any":
        # Avoid creating a duplicate span if run() is invoked after stream().
        # This fails here: https://github.com/pydantic/pydantic-ai/blob/916fc83e8929470679db5ac1b3065bda5d5f4253/pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1119
        did_stream = getattr(self, "_did_stream", False)
        # Do not create a duplicate span when a cached result is served.
        cached_result = getattr(self, "_result", None)
        if did_stream or cached_result is not None:
            return await original_model_request_run(self, ctx)

        messages, model, model_settings = extract_graph_request_data(self, ctx)

        with ai_client_span(messages, None, model, model_settings) as span:
            result = await original_model_request_run(self, ctx)

            # Extract response from result if available
            model_response: "Optional[ModelResponse]" = None
            if hasattr(result, "model_response"):
                model_response = result.model_response

            update_ai_client_span(span, model_response)
            return result

    ModelRequestNode.run = wrapped_model_request_run  # type: ignore[method-assign]

    # Patch ModelRequestNode.stream for streaming requests
    original_model_request_stream = ModelRequestNode.stream

    def create_wrapped_stream(
        original_stream_method: "Callable[..., Any]",
    ) -> "Callable[..., Any]":
        """Create a wrapper for ModelRequestNode.stream that creates chat spans."""

        @asynccontextmanager
        @wraps(original_stream_method)
        async def wrapped_model_request_stream(self: "Any", ctx: "Any") -> "Any":
            # Avoid creating a duplicate span if the function is invoked twice.
            # This fails here: https://github.com/pydantic/pydantic-ai/blob/916fc83e8929470679db5ac1b3065bda5d5f4253/pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1128
            did_stream = getattr(self, "_did_stream", False)
            if did_stream:
                async with original_stream_method(self, ctx) as stream:
                    yield stream
                return

            messages, model, model_settings = extract_graph_request_data(self, ctx)

            # Create chat span for streaming request
            with ai_client_span(messages, None, model, model_settings) as span:
                # Call the original stream method
                async with original_stream_method(self, ctx) as stream:
                    yield stream

                # After streaming completes, update span with response data
                # The ModelRequestNode stores the final response in _result
                model_response: "Optional[ModelResponse]" = None
                if hasattr(self, "_result") and self._result is not None:
                    # _result is a NextNode containing the model_response
                    if hasattr(self._result, "model_response"):
                        model_response = self._result.model_response

                update_ai_client_span(span, model_response)

        return wrapped_model_request_stream

    ModelRequestNode.stream = create_wrapped_stream(original_model_request_stream)  # type: ignore[method-assign]

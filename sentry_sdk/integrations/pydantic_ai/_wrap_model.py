"""Chat-span emission for model requests.

Two backends, selected once in _compat, implement the same duty — emit a
gen_ai.chat span around each model request:

- "hooks" (pydantic-ai >= 1.73): request hooks registered through
  pydantic_ai.capabilities, paired per run through RunContext.metadata.
- "graph_nodes" (older versions): direct patching of ModelRequestNode.
"""

import functools
from contextlib import asynccontextmanager
from functools import wraps
from typing import TYPE_CHECKING

from sentry_sdk.utils import capture_internal_exceptions

from ._compat import MODEL_BACKEND, Agent
from ._extract import extract_graph_request_data
from ._spans import ai_client_span, update_ai_client_span

if TYPE_CHECKING:
    from typing import Any, Callable, Optional

    from pydantic_ai import ModelRequestContext, RunContext
    from pydantic_ai.capabilities import Hooks as HooksType
    from pydantic_ai.messages import ModelResponse


def install_model_backend() -> None:
    """Install the model-request instrumentation for the installed version."""
    if MODEL_BACKEND == "hooks":
        _install_request_hooks()
    elif MODEL_BACKEND == "graph_nodes":
        _patch_graph_nodes()


def _install_request_hooks() -> None:
    """
    Creates hooks for chat model calls and registers them by adding them to the
    `capabilities` argument passed to `Agent.__init__()`.

    The chat span opened in on_request is stored in the run's
    `RunContext.metadata` dict, which pydantic-ai shares by reference between
    the hooks of one run. This keeps span pairing correct per run (even for
    overlapping runs in one task). It requires seeding a metadata dict in
    `patched_init` below (and in the run wrappers, see _wrap_agent.py) when
    the user did not provide one.
    """
    from pydantic_ai.capabilities import Hooks

    hooks: "HooksType" = Hooks()

    @hooks.on.before_model_request
    async def on_request(
        ctx: "RunContext[None]", request_context: "ModelRequestContext"
    ) -> "ModelRequestContext":
        run_context_metadata = ctx.metadata
        if not isinstance(run_context_metadata, dict):
            return request_context

        span = None
        with capture_internal_exceptions():
            span = ai_client_span(
                messages=request_context.messages,
                agent=None,
                model=request_context.model,
                model_settings=request_context.model_settings,
            )

        if span is None:
            return request_context

        run_context_metadata["_sentry_span"] = span
        span.__enter__()

        return request_context

    @hooks.on.after_model_request
    async def on_response(
        ctx: "RunContext[None]",
        *,
        request_context: "ModelRequestContext",
        response: "ModelResponse",
    ) -> "ModelResponse":
        run_context_metadata = ctx.metadata
        if not isinstance(run_context_metadata, dict):
            return response

        span = run_context_metadata.pop("_sentry_span", None)
        if span is None:
            return response

        with capture_internal_exceptions():
            update_ai_client_span(span, response)
        with capture_internal_exceptions():
            span.__exit__(None, None, None)

        return response

    @hooks.on.model_request_error
    async def on_error(
        ctx: "RunContext[None]",
        *,
        request_context: "ModelRequestContext",
        error: "Exception",
    ) -> "ModelResponse":
        run_context_metadata = ctx.metadata

        if not isinstance(run_context_metadata, dict):
            raise error

        span = run_context_metadata.pop("_sentry_span", None)
        if span is None:
            raise error

        with capture_internal_exceptions():
            span.__exit__(type(error), error, error.__traceback__)

        raise error

    original_init = Agent.__init__

    @functools.wraps(original_init)
    def patched_init(self: "Agent[Any, Any]", *args: "Any", **kwargs: "Any") -> None:
        caps = list(kwargs.get("capabilities") or [])
        caps.append(hooks)
        kwargs["capabilities"] = caps

        metadata = kwargs.get("metadata")
        if metadata is None:
            kwargs["metadata"] = {}  # Used as shared reference between hooks

        return original_init(self, *args, **kwargs)

    Agent.__init__ = patched_init  # type: ignore[method-assign]


def _patch_graph_nodes() -> None:
    """
    Patches the graph node execution to create chat spans on pydantic-ai
    versions that predate the request hooks.

    ModelRequestNode -> Creates ai_client span for model requests
    """
    try:
        from pydantic_ai._agent_graph import ModelRequestNode
    except ImportError:
        # Private module moved or renamed; degrade to agent + tool spans
        # rather than crashing setup.
        return

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

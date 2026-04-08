import functools

from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import pydantic_ai  # type: ignore # noqa: F401
    from pydantic_ai import Agent
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


from .patches import (
    _patch_agent_run,
    _patch_graph_nodes,
    _patch_model_request,
    _patch_tool_execution,
)

from .spans.ai_client import ai_client_span, update_ai_client_span

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from pydantic_ai import ModelRequestContext, RunContext
    from pydantic_ai.messages import ModelResponse  # type: ignore


class PydanticAIIntegration(Integration):
    identifier = "pydantic_ai"
    origin = f"auto.ai.{identifier}"
    are_request_hooks_available = True

    def __init__(
        self, include_prompts: bool = True, handled_tool_call_exceptions: bool = True
    ) -> None:
        """
        Initialize the Pydantic AI integration.

        Args:
            include_prompts: Whether to include prompts and messages in span data.
                Requires send_default_pii=True. Defaults to True.
            handled_tool_exceptions: Capture tool call exceptions that Pydantic AI
                internally prevents from bubbling up.
        """
        self.include_prompts = include_prompts
        self.handled_tool_call_exceptions = handled_tool_call_exceptions

    @staticmethod
    def setup_once() -> None:
        """
        Set up the pydantic-ai integration.

        This patches the key methods in pydantic-ai to create Sentry spans for:
        - Agent invocations (Agent.run methods)
        - Model requests (AI client calls)
        - Tool executions
        """
        _patch_agent_run()

        try:
            from pydantic_ai.capabilities import Hooks  # type: ignore
        except ImportError:
            Hooks = None
            PydanticAIIntegration.are_request_hooks_available = False

        if Hooks is None:
            _patch_graph_nodes()
            _patch_model_request()
            return

        _patch_tool_execution()

        # Assumptions:
        # - Model requests within a run are sequential.
        # - ctx.metadata is a shared dict instance between hooks.
        hooks = Hooks()

        @hooks.on.before_model_request  # type: ignore
        async def on_request(
            ctx: "RunContext[None]", request_context: "ModelRequestContext"
        ) -> "ModelRequestContext":
            span = ai_client_span(
                messages=request_context.messages,
                agent=None,
                model=request_context.model,
                model_settings=request_context.model_settings,
            )
            run_context_metadata = ctx.metadata
            if isinstance(run_context_metadata, dict):
                run_context_metadata["_sentry_span"] = span

            span.__enter__()

            return request_context

        @hooks.on.after_model_request  # type: ignore
        async def on_response(
            ctx: "RunContext[None]",
            *,
            request_context: "ModelRequestContext",
            response: "ModelResponse",
        ) -> "ModelResponse":
            run_context_metadata = ctx.metadata
            if not isinstance(run_context_metadata, dict):
                return response

            span = run_context_metadata["_sentry_span"]
            if span is None:
                return response

            update_ai_client_span(span, response)
            span.__exit__(None, None, None)
            del run_context_metadata["_sentry_span"]

            return response

        @hooks.on.model_request_error  # type: ignore
        async def on_error(
            ctx: "RunContext[None]",
            *,
            request_context: "ModelRequestContext",
            error: "Exception",
        ) -> "ModelResponse":
            run_context_metadata = ctx.metadata
            if isinstance(run_context_metadata, dict):
                span = run_context_metadata.pop("_sentry_span", None)
                if span is not None:
                    span.__exit__(type(error), error, error.__traceback__)
            raise error

        original_init = Agent.__init__

        @functools.wraps(original_init)
        def patched_init(
            self: "Agent[Any, Any]", *args: "Any", **kwargs: "Any"
        ) -> None:
            caps = list(kwargs.get("capabilities") or [])
            caps.append(hooks)
            kwargs["capabilities"] = caps
            return original_init(self, *args, **kwargs)

        Agent.__init__ = patched_init

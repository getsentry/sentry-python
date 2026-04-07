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
    from pydantic_ai import ModelRequestContext, RunContext
    from pydantic_ai.messages import ModelResponse


class PydanticAIIntegration(Integration):
    identifier = "pydantic_ai"
    origin = f"auto.ai.{identifier}"

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
            from pydantic_ai.capabilities import Hooks

            hooks = Hooks()

            @hooks.on.before_model_request
            async def on_request(
                ctx: "RunContext[None]", request_context: "ModelRequestContext"
            ) -> "ModelRequestContext":
                span = ai_client_span(
                    messages=request_context.messages,
                    agent=None,
                    model=request_context.model,
                    model_settings=request_context.model_settings,
                )
                ctx.metadata["_sentry_span"] = span
                span.__enter__()

                return request_context

            @hooks.on.after_model_request
            async def on_response(
                ctx: "RunContext[None]",
                *,
                request_context: "ModelRequestContext",
                response: "ModelResponse",
            ) -> "ModelResponse":
                span = ctx.metadata["_sentry_span"]
                if span is None:
                    return response

                update_ai_client_span(span, response)
                span.__exit__(None, None, None)
                del ctx.metadata["_sentry_span"]

                return response

            original_init = Agent.__init__

            @functools.wraps(original_init)
            def patched_init(self, *args, **kwargs):
                caps = list(kwargs.get("capabilities") or [])
                caps.append(hooks)
                kwargs["capabilities"] = caps
                original_init(self, *args, **kwargs)

            Agent.__init__ = patched_init

        except ImportError:
            _patch_graph_nodes()
            _patch_model_request()

        _patch_tool_execution()

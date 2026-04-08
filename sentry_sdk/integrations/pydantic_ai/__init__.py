from functools import wraps
from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import pydantic_ai  # type: ignore # noqa: F401
    from pydantic_ai.capabilities.combined import CombinedCapability  # type: ignore
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


from .patches import (
    _patch_agent_run,
    _patch_tool_execution,
)
from .spans import (
    ai_client_span,
    update_ai_client_span,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable

    from pydantic_ai._run_context import RunContext  # type: ignore
    from pydantic_ai.models import ModelRequestContext  # type: ignore
    from pydantic_ai.messages import ModelResponse  # type: ignore


def _patch_wrap_model_request() -> None:
    original_wrap_model_request = CombinedCapability.wrap_model_request

    @wraps(original_wrap_model_request)
    async def wrapped_wrap_model_request(
        self: "CombinedCapability",
        ctx: "RunContext[Any]",
        *,
        request_context: "ModelRequestContext",
        handler: "Callable[[ModelRequestContext], Awaitable[ModelResponse]]",
    ) -> "Any":
        with ai_client_span(
            request_context.messages,
            None,
            request_context.model,
            request_context.model_settings,
        ) as span:
            result = await original_wrap_model_request(
                self, ctx, request_context=request_context, handler=handler
            )

            update_ai_client_span(span, result)
            return result

    CombinedCapability.wrap_model_request = wrapped_wrap_model_request


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
        _patch_wrap_model_request()
        _patch_tool_execution()

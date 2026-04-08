from functools import wraps
from contextlib import asynccontextmanager
from contextvars import ContextVar
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.consts import SPANDATA

try:
    import pydantic_ai  # type: ignore # noqa: F401
    from pydantic_ai.capabilities.combined import CombinedCapability  # type: ignore
    from pydantic_ai._agent_graph import ModelRequestNode
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

    from pydantic_ai._run_context import RunContext
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.messages import ModelResponse


_is_streaming: ContextVar[bool] = ContextVar(
    "sentry_pydantic_ai_is_streaming", default=False
)


def _patch_wrap_model_request():
    original_wrap_model_request = CombinedCapability.wrap_model_request

    @wraps(original_wrap_model_request)
    async def wrapped_wrap_model_request(
        self,
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
            span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, _is_streaming.get())

            result = await original_wrap_model_request(
                self, ctx, request_context=request_context, handler=handler
            )

            update_ai_client_span(span, result)
            return result

    CombinedCapability.wrap_model_request = wrapped_wrap_model_request


def _patch_model_request_node_run():
    original_model_request_run = ModelRequestNode.run

    @wraps(original_model_request_run)
    async def wrapped_model_request_run(self: "Any", ctx: "Any") -> "Any":
        token = _is_streaming.set(False)
        try:
            return await original_model_request_run(self, ctx)
        finally:
            _is_streaming.reset(token)

    ModelRequestNode.run = wrapped_model_request_run


def _patch_model_request_node_stream():
    original_model_request_stream = ModelRequestNode.stream

    def create_wrapped_stream(
        original_stream_method: "Callable[..., Any]",
    ) -> "Callable[..., Any]":
        @asynccontextmanager
        @wraps(original_stream_method)
        async def wrapped_model_request_stream(self: "Any", ctx: "Any") -> "Any":
            token = _is_streaming.set(True)
            try:
                async with original_stream_method(self, ctx) as stream:
                    yield stream
            finally:
                _is_streaming.reset(token)

        return wrapped_model_request_stream

    ModelRequestNode.stream = create_wrapped_stream(original_model_request_stream)


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

        _patch_model_request_node_run()
        _patch_model_request_node_stream()

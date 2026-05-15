import functools

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import capture_internal_exceptions

try:
    import pydantic_ai  # type: ignore # noqa: F401
    from pydantic_ai import Agent
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


from typing import TYPE_CHECKING

from .patches import (
    _patch_agent_run,
    _patch_graph_nodes,
    _patch_tool_execution,
)
from .spans.ai_client import ai_client_span, update_ai_client_span

if TYPE_CHECKING:
    from typing import Any

    from pydantic_ai import ModelRequestContext, RunContext
    from pydantic_ai.capabilities import Hooks  # type: ignore
    from pydantic_ai.messages import ModelResponse  # type: ignore


def register_hooks(hooks: "Hooks") -> None:
    """
    Creates hooks for chat model calls and register the hooks by adding the hooks to the `capabilities` argument passed to `Agent.__init__()`.
    """

    @hooks.on.before_model_request  # type: ignore
    async def on_request(
        ctx: "RunContext[None]", request_context: "ModelRequestContext"
    ) -> "ModelRequestContext":
        run_context_metadata = ctx.metadata
        if not isinstance(run_context_metadata, dict):
            return request_context

        span = ai_client_span(
            messages=request_context.messages,
            agent=None,
            model=request_context.model,
            model_settings=request_context.model_settings,
        )

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

        span = run_context_metadata.pop("_sentry_span", None)
        if span is None:
            return response

        update_ai_client_span(span, response)
        span.__exit__(None, None, None)

        return response

    @hooks.on.model_request_error  # type: ignore
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

    Agent.__init__ = patched_init


class PydanticAIIntegration(Integration):
    """
    Typical interaction with the library:
    1. The user creates an Agent instance with configuration, including system instructions sent to every model call.
    2. The user calls `Agent.run()` or `Agent.run_stream()` to start an agent run. The latter can be used to incrementally receive progress.
    - Each run invocation has `RunContext` objects that are passed to the library hooks.
    3. In a loop, the agent repeatedly calls the model, maintaining a conversation history that includes previous messages and tool results, which is passed to each call.

    Internally, Pydantic AI maintains an execution graph in which ModelRequestNode are responsible for model calls, including retries.
    Hooks using the decorators provided by `pydantic_ai.capabilities` create and manage spans for model calls when these hooks are available (newer library versions).
    The span is created in `on_request` and stored in the metadata of the `RunContext` object shared with `on_response` and `on_error`.

    The metadata dictionary on the RunContext instance is initialized with `{"_sentry_span": None}` in the `_create_run_wrapper()` and `_create_streaming_wrapper()` wrappers that
    instrument `Agent.run()` and `Agent.run_stream()`, respectively. A non-empty dictionary is required for the metadata object to be a shared reference between hooks.
    """

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
        _patch_tool_execution()

        try:
            from pydantic_ai.capabilities import Hooks
        except ImportError:
            Hooks = None
            PydanticAIIntegration.are_request_hooks_available = False

        if Hooks is None:
            _patch_graph_nodes()
            return

        hooks = Hooks()
        register_hooks(hooks)

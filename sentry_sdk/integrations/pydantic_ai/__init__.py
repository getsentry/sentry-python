import functools
import sys

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.utils import capture_internal_exceptions, parse_version, reraise

from .spans import execute_tool_span, update_execute_tool_span
from .utils import _capture_exception

try:
    import pydantic_ai  # noqa: F401
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ToolRetryError
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from .spans.ai_client import ai_client_span, update_ai_client_span
from .spans.invoke_agent import invoke_agent_span, update_invoke_agent_span

if TYPE_CHECKING:
    from typing import Any

    from pydantic import ValidationError
    from pydantic_ai import (
        ModelRequestContext,
        ModelRetry,
        RunContext,
        ToolCallPart,
        ToolDefinition,
    )
    from pydantic_ai.agent import AgentRunResult
    from pydantic_ai.capabilities import (
        Hooks,
        RawToolArgs,
        ValidatedToolArgs,
        WrapModelRequestHandler,
        WrapRunHandler,
        WrapToolExecuteHandler,
    )
    from pydantic_ai.messages import ModelResponse


def register_hooks(hooks: "Hooks") -> None:
    """
    Creates hooks for chat model calls and register the hooks by adding the hooks to the `capabilities` argument passed to `Agent.__init__()`.
    """

    @hooks.on.model_request
    async def on_model_request(
        ctx: "RunContext[None]",
        *,
        request_context: "ModelRequestContext",
        handler: "WrapModelRequestHandler",
    ) -> "ModelResponse":
        with ai_client_span(
            messages=request_context.messages,
            agent=ctx.agent,
            model=request_context.model,
            model_settings=request_context.model_settings,
        ) as span:
            response = await handler(request_context)

            update_ai_client_span(span, response)
            return response

    @hooks.on.tool_validate_error
    async def sentry_on_tool_validate_error(
        ctx: "RunContext[Any]",
        *,
        call: "ToolCallPart",
        tool_def: "ToolDefinition",
        args: "RawToolArgs",
        error: "ValidationError | ModelRetry",
    ) -> "ValidatedToolArgs":
        with capture_internal_exceptions():
            integration = sentry_sdk.get_client().get_integration(
                PydanticAIIntegration,
            )
            if integration is not None and integration.handled_tool_call_exceptions:
                _capture_exception(error, handled=True)

        raise error

    @hooks.on.tool_execute
    async def sentry_wrap_tool_execute(
        ctx: "RunContext[Any]",
        *,
        call: "ToolCallPart",
        tool_def: "ToolDefinition",
        args: "ValidatedToolArgs",
        handler: "WrapToolExecuteHandler",
    ) -> "Any":
        with execute_tool_span(
            call.tool_name,
            args,
            ctx.agent,
            tool_definition=tool_def,
        ) as span:
            try:
                result = await handler(args)
                update_execute_tool_span(span, result)
                return result
            except ToolRetryError as exc:
                exc_info = sys.exc_info()
                with capture_internal_exceptions():
                    integration = sentry_sdk.get_client().get_integration(
                        PydanticAIIntegration,
                    )
                    if (
                        integration is not None
                        and integration.handled_tool_call_exceptions
                    ):
                        _capture_exception(exc, handled=True)
                reraise(*exc_info)

    @hooks.on.run
    async def sentry_wrap_run(
        ctx: "RunContext[Any]",
        *,
        handler: "WrapRunHandler",
    ) -> "AgentRunResult[Any]":
        with invoke_agent_span(
            user_prompt=ctx.prompt,
            agent=ctx.agent,
            model=ctx.model,
            model_settings=ctx.model_settings,
        ) as span:
            try:
                result = await handler()
                update_invoke_agent_span(span, result)
                return result
            except Exception as exc:
                exc_info = sys.exc_info()
                with capture_internal_exceptions():
                    _capture_exception(exc, handled=False)
                reraise(*exc_info)

    original_init = Agent.__init__

    @functools.wraps(original_init)
    def patched_init(self: "Agent[Any, Any]", *args: "Any", **kwargs: "Any") -> None:
        caps = list(kwargs.get("capabilities") or [])
        caps.append(hooks)
        kwargs["capabilities"] = caps

        return original_init(self, *args, **kwargs)

    Agent.__init__ = patched_init  # type: ignore[method-assign]


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
        try:
            PYDANTIC_AI_VERSION = version("pydantic-ai-slim")
        except PackageNotFoundError:
            return

        PYDANTIC_AI_VERSION = parse_version(PYDANTIC_AI_VERSION)
        _check_minimum_version(PydanticAIIntegration, PYDANTIC_AI_VERSION)
        if PYDANTIC_AI_VERSION is None:
            return

        try:
            from pydantic_ai.capabilities import Hooks
        except ImportError:
            return

        hooks = Hooks()
        register_hooks(hooks)

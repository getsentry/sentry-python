from functools import wraps

import sentry_sdk
from sentry_sdk.tracing_utils import set_span_errored
from sentry_sdk.utils import event_from_exception

from ..spans import invoke_agent_span, update_invoke_agent_span

from typing import TYPE_CHECKING
from pydantic_ai.agent import Agent  # type: ignore

if TYPE_CHECKING:
    from typing import Any, Callable, Optional


def _capture_exception(exc):
    # type: (Any) -> None
    set_span_errored()

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "pydantic_ai", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


class _StreamingContextManagerWrapper:
    """Wrapper for streaming methods that return async context managers."""

    def __init__(
        self,
        agent,
        original_ctx_manager,
        user_prompt,
        model,
        model_settings,
        is_streaming=True,
    ):
        # type: (Any, Any, Any, Any, Any, bool) -> None
        self.agent = agent
        self.original_ctx_manager = original_ctx_manager
        self.user_prompt = user_prompt
        self.model = model
        self.model_settings = model_settings
        self.is_streaming = is_streaming
        self._isolation_scope = None  # type: Any
        self._span = None  # type: Optional[sentry_sdk.tracing.Span]
        self._result = None  # type: Any

    async def __aenter__(self):
        # type: () -> Any
        # Set up isolation scope and invoke_agent span
        self._isolation_scope = sentry_sdk.isolation_scope()
        self._isolation_scope.__enter__()

        # Store agent reference and streaming flag
        sentry_sdk.get_current_scope().set_context(
            "pydantic_ai_agent", {"_agent": self.agent, "_streaming": self.is_streaming}
        )

        # Create invoke_agent span (will be closed in __aexit__)
        self._span = invoke_agent_span(
            self.user_prompt, self.agent, self.model, self.model_settings
        )
        self._span.__enter__()

        # Enter the original context manager
        result = await self.original_ctx_manager.__aenter__()
        self._result = result
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # type: (Any, Any, Any) -> None
        try:
            # Exit the original context manager first
            await self.original_ctx_manager.__aexit__(exc_type, exc_val, exc_tb)

            # Update span with output if successful
            if exc_type is None and self._result and hasattr(self._result, "output"):
                output = (
                    self._result.output if hasattr(self._result, "output") else None
                )
                if self._span is not None:
                    update_invoke_agent_span(self._span, output)
        finally:
            sentry_sdk.get_current_scope().remove_context("pydantic_ai_agent")
            # Clean up invoke span
            if self._span:
                self._span.__exit__(exc_type, exc_val, exc_tb)

            # Clean up isolation scope
            if self._isolation_scope:
                self._isolation_scope.__exit__(exc_type, exc_val, exc_tb)


def _create_run_wrapper(original_func, is_streaming=False):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]
    """
    Wraps the Agent.run method to create an invoke_agent span.

    Args:
        original_func: The original run method
        is_streaming: Whether this is a streaming method (for future use)
    """

    @wraps(original_func)
    async def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Isolate each workflow so that when agents are run in asyncio tasks they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            # Store agent reference and streaming flag in Sentry scope for access in nested spans
            # We store the full agent to allow access to tools and system prompts
            sentry_sdk.get_current_scope().set_context(
                "pydantic_ai_agent", {"_agent": self, "_streaming": is_streaming}
            )

            # Extract parameters for the span
            user_prompt = kwargs.get("user_prompt") or (args[0] if args else None)
            model = kwargs.get("model")
            model_settings = kwargs.get("model_settings")

            # Create invoke_agent span
            with invoke_agent_span(user_prompt, self, model, model_settings) as span:
                try:
                    result = await original_func(self, *args, **kwargs)

                    # Update span with output
                    output = result.output if hasattr(result, "output") else None
                    update_invoke_agent_span(span, output)

                    return result
                except Exception as exc:
                    _capture_exception(exc)
                    raise exc from None
                finally:
                    sentry_sdk.get_current_scope().remove_context("pydantic_ai_agent")

    return wrapper


def _create_streaming_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps run_stream method that returns an async context manager.
    """

    @wraps(original_func)
    def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Extract parameters for the span
        user_prompt = kwargs.get("user_prompt") or (args[0] if args else None)
        model = kwargs.get("model")
        model_settings = kwargs.get("model_settings")

        # Call original function to get the context manager
        original_ctx_manager = original_func(self, *args, **kwargs)

        # Wrap it with our instrumentation
        return _StreamingContextManagerWrapper(
            agent=self,
            original_ctx_manager=original_ctx_manager,
            user_prompt=user_prompt,
            model=model,
            model_settings=model_settings,
            is_streaming=True,
        )

    return wrapper


def _create_streaming_events_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps run_stream_events method - no span needed as it delegates to run().

    Note: run_stream_events internally calls self.run() with an event_stream_handler,
    so the invoke_agent span will be created by the run() wrapper.
    """

    @wraps(original_func)
    async def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Just call the original generator - it will call run() which has the instrumentation
        try:
            async for event in original_func(self, *args, **kwargs):
                yield event
        except Exception as exc:
            _capture_exception(exc)
            raise exc from None

    return wrapper


def _patch_agent_run():
    # type: () -> None
    """
    Patches the Agent run methods to create spans for agent execution.

    This patches both non-streaming (run, run_sync) and streaming
    (run_stream, run_stream_events) methods.
    """

    # Store original methods
    original_run = Agent.run
    original_run_stream = Agent.run_stream
    original_run_stream_events = Agent.run_stream_events

    # Wrap and apply patches for non-streaming methods
    Agent.run = _create_run_wrapper(original_run, is_streaming=False)

    # Wrap and apply patches for streaming methods
    Agent.run_stream = _create_streaming_wrapper(original_run_stream)
    Agent.run_stream_events = _create_streaming_events_wrapper(
        original_run_stream_events
    )

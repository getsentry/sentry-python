from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from ..spans import agent_workflow_span, invoke_agent_span, update_invoke_agent_span
from ..utils import _capture_exception

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Optional

try:
    import pydantic_ai
    from pydantic_ai.agent import Agent
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


class _StreamingContextManagerWrapper:
    """Wrapper for streaming methods that return async context managers."""

    def __init__(self, agent, original_ctx_manager, is_streaming=True):
        # type: (Any, Any, bool) -> None
        self.agent = agent
        self.original_ctx_manager = original_ctx_manager
        self.is_streaming = is_streaming
        self._isolation_scope = None  # type: Any
        self._workflow_span = None  # type: Optional[sentry_sdk.tracing.Span]

    async def __aenter__(self):
        # type: () -> Any
        # Set up isolation scope and workflow span
        self._isolation_scope = sentry_sdk.isolation_scope()
        self._isolation_scope.__enter__()

        # Store agent reference and streaming flag
        sentry_sdk.get_current_scope().set_context(
            "pydantic_ai_agent", {"_agent": self.agent, "_streaming": self.is_streaming}
        )

        # Create workflow span
        self._workflow_span = agent_workflow_span(self.agent)
        self._workflow_span.__enter__()

        # Enter the original context manager
        result = await self.original_ctx_manager.__aenter__()
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # type: (Any, Any, Any) -> None
        try:
            # Exit the original context manager first
            await self.original_ctx_manager.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            # Clean up workflow span
            if self._workflow_span:
                self._workflow_span.__exit__(exc_type, exc_val, exc_tb)

            # Clean up isolation scope
            if self._isolation_scope:
                self._isolation_scope.__exit__(exc_type, exc_val, exc_tb)


def _create_run_wrapper(original_func, is_streaming=False):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]
    """
    Wraps the Agent.run method to create a root span for the agent workflow.

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

            with agent_workflow_span(self):
                result = None
                try:
                    result = await original_func(self, *args, **kwargs)
                    return result
                except Exception as exc:
                    _capture_exception(exc)

                    # It could be that there is an "invoke agent" span still open
                    current_span = sentry_sdk.get_current_span()
                    if current_span is not None and current_span.timestamp is None:
                        current_span.__exit__(None, None, None)

                    raise exc from None

    return wrapper


def _create_run_sync_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps the Agent.run_sync method to create a root span for the agent workflow.
    Note: run_sync is always non-streaming.
    """

    @wraps(original_func)
    def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Isolate each workflow so that when agents are run they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            # Store agent reference and streaming flag in Sentry scope for access in nested spans
            # We store the full agent to allow access to tools and system prompts
            sentry_sdk.get_current_scope().set_context(
                "pydantic_ai_agent", {"_agent": self, "_streaming": False}
            )

            with agent_workflow_span(self):
                result = None
                try:
                    result = original_func(self, *args, **kwargs)
                    return result
                except Exception as exc:
                    _capture_exception(exc)

                    # It could be that there is an "invoke agent" span still open
                    current_span = sentry_sdk.get_current_span()
                    if current_span is not None and current_span.timestamp is None:
                        current_span.__exit__(None, None, None)

                    raise exc from None

    return wrapper


def _create_streaming_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps run_stream method that returns an async context manager.
    """

    @wraps(original_func)
    def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Call original function to get the context manager
        original_ctx_manager = original_func(self, *args, **kwargs)

        # Wrap it with our instrumentation
        return _StreamingContextManagerWrapper(
            agent=self, original_ctx_manager=original_ctx_manager, is_streaming=True
        )

    return wrapper


def _create_streaming_events_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps run_stream_events method that returns an async generator/iterator.
    """

    @wraps(original_func)
    async def wrapper(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        # Isolate each workflow so that when agents are run in asyncio tasks they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            # Store agent reference and streaming flag in Sentry scope for access in nested spans
            sentry_sdk.get_current_scope().set_context(
                "pydantic_ai_agent", {"_agent": self, "_streaming": True}
            )

            with agent_workflow_span(self):
                try:
                    # Call the original generator and yield all events
                    async for event in original_func(self, *args, **kwargs):
                        yield event
                except Exception as exc:
                    _capture_exception(exc)

                    # It could be that there is an "invoke agent" span still open
                    current_span = sentry_sdk.get_current_span()
                    if current_span is not None and current_span.timestamp is None:
                        current_span.__exit__(None, None, None)

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
    original_run_sync = Agent.run_sync
    original_run_stream = Agent.run_stream
    original_run_stream_events = Agent.run_stream_events

    # Wrap and apply patches for non-streaming methods
    Agent.run = _create_run_wrapper(original_run, is_streaming=False)  # type: ignore
    Agent.run_sync = _create_run_sync_wrapper(original_run_sync)  # type: ignore

    # Wrap and apply patches for streaming methods
    Agent.run_stream = _create_streaming_wrapper(original_run_stream)  # type: ignore
    Agent.run_stream_events = _create_streaming_events_wrapper(  # type: ignore[method-assign]
        original_run_stream_events
    )

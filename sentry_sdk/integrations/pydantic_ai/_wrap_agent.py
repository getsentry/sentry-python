"""Instrumentation of the Agent.run / Agent.run_stream entry points."""

import sys
from contextlib import ExitStack
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.utils import capture_internal_exceptions, reraise

from ._compat import USES_REQUEST_HOOKS, Agent
from ._run_context import agent_run_scope
from ._spans import (
    _capture_exception,
    invoke_agent_span,
    update_invoke_agent_span,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Optional, Union


def _extract_run_params(
    args: "tuple[Any, ...]", kwargs: "dict[str, Any]"
) -> "tuple[Any, Any, Any]":
    """Extract (user_prompt, model, model_settings) from a run call."""
    user_prompt = kwargs.get("user_prompt") or (args[0] if args else None)
    return user_prompt, kwargs.get("model"), kwargs.get("model_settings")


def _seed_run_metadata(kwargs: "dict[str, Any]") -> None:
    """Seed the run's metadata dict when the request hooks are in use.

    The hooks pair each chat span with its model request through the run's
    RunContext.metadata dict (see _wrap_model.py), which requires the
    metadata object to be a dict shared by reference between hooks.
    """
    if USES_REQUEST_HOOKS and kwargs.get("metadata") is None:
        kwargs["metadata"] = {"_sentry_span": None}


class _StreamingContextManagerWrapper:
    """Wrapper for streaming methods that return async context managers."""

    def __init__(
        self,
        agent: "Any",
        original_ctx_manager: "Any",
        user_prompt: "Any",
        model: "Any",
        model_settings: "Any",
    ) -> None:
        self.agent = agent
        self.original_ctx_manager = original_ctx_manager
        self.user_prompt = user_prompt
        self.model = model
        self.model_settings = model_settings
        self._contexts: "Optional[ExitStack]" = None
        self._span: "Optional[Union[sentry_sdk.tracing.Span, sentry_sdk.traces.StreamedSpan]]" = None
        self._result: "Any" = None

    async def __aenter__(self) -> "Any":
        # Isolation scope, invoke_agent span, and run-context tracking are all
        # owned by one ExitStack so they unwind together in __aexit__ (or
        # right here if entering the original context manager fails).
        with ExitStack() as contexts:
            contexts.enter_context(sentry_sdk.isolation_scope())
            span = invoke_agent_span(
                self.user_prompt,
                self.agent,
                self.model,
                self.model_settings,
                is_streaming=True,
            )
            contexts.enter_context(span)
            self._span = span
            contexts.enter_context(agent_run_scope(self.agent, is_streaming=True))

            result = await self.original_ctx_manager.__aenter__()

            self._contexts = contexts.pop_all()

        self._result = result
        return result

    async def __aexit__(self, exc_type: "Any", exc_val: "Any", exc_tb: "Any") -> "Any":
        try:
            # Exit the original context manager first; propagate its exception
            # suppression so the integration never changes control flow.
            suppressed = await self.original_ctx_manager.__aexit__(
                exc_type, exc_val, exc_tb
            )
            if suppressed:
                exc_type = exc_val = exc_tb = None

            # Update span with result if successful
            if exc_type is None and self._result and self._span is not None:
                update_invoke_agent_span(self._span, self._result)

            return suppressed
        finally:
            if self._contexts is not None:
                self._contexts.__exit__(exc_type, exc_val, exc_tb)


def _create_run_wrapper(original_func: "Callable[..., Any]") -> "Callable[..., Any]":
    """
    Wraps the Agent.run method to create an invoke_agent span.
    """

    @wraps(original_func)
    async def wrapper(self: "Any", *args: "Any", **kwargs: "Any") -> "Any":
        user_prompt, model, model_settings = _extract_run_params(args, kwargs)
        _seed_run_metadata(kwargs)

        # Isolate each workflow so that when agents are run in asyncio tasks
        # they don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            with invoke_agent_span(
                user_prompt, self, model, model_settings, is_streaming=False
            ) as span:
                with agent_run_scope(self, is_streaming=False):
                    try:
                        result = await original_func(self, *args, **kwargs)

                        update_invoke_agent_span(span, result)

                        return result
                    except Exception as exc:
                        exc_info = sys.exc_info()
                        with capture_internal_exceptions():
                            _capture_exception(exc)
                        reraise(*exc_info)

    return wrapper


def _create_streaming_wrapper(
    original_func: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """
    Wraps run_stream method that returns an async context manager.
    """

    @wraps(original_func)
    def wrapper(self: "Any", *args: "Any", **kwargs: "Any") -> "Any":
        user_prompt, model, model_settings = _extract_run_params(args, kwargs)
        _seed_run_metadata(kwargs)

        # Call original function to get the context manager
        original_ctx_manager = original_func(self, *args, **kwargs)

        # Wrap it with our instrumentation
        return _StreamingContextManagerWrapper(
            agent=self,
            original_ctx_manager=original_ctx_manager,
            user_prompt=user_prompt,
            model=model,
            model_settings=model_settings,
        )

    return wrapper


def _patch_agent_run() -> None:
    """
    Patches the Agent run methods to create spans for agent execution.

    This patches both the non-streaming (run) and streaming (run_stream)
    entry points; run_sync delegates to run.
    """

    # Store original methods
    original_run = Agent.run
    original_run_stream = Agent.run_stream

    # Wrap and apply patches for non-streaming methods
    Agent.run = _create_run_wrapper(original_run)  # type: ignore[method-assign]

    # Wrap and apply patches for streaming methods
    Agent.run_stream = _create_streaming_wrapper(original_run_stream)  # type: ignore[method-assign]

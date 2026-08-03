import sys
from functools import wraps

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.utils import capture_internal_exceptions, reraise

from ..spans import (
    agent_workflow_span,
    execute_tool_span,
    update_execute_tool_span,
    update_invoke_agent_span,
)
from ..utils import _capture_exception

try:
    from agents import FunctionTool, RunHooks
    from agents.exceptions import AgentsException
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, Callable

    from agents import Agent, Tool, ToolContext


TContext = TypeVar("TContext")


class _SentryRunHooks(RunHooks[TContext]):  # type: ignore[misc]
    async def on_tool_start(
        self,
        context: "ToolContext[TContext]",
        agent: "Agent[TContext]",
        tool: "Tool",
    ) -> "None":
        if not isinstance(tool, FunctionTool):
            return

        span = execute_tool_span(tool, agent)
        span.__enter__()
        context.sentry_tool_span = span

        if not should_send_default_pii():
            return

        if isinstance(span, StreamedSpan):
            span.set_attribute(SPANDATA.GEN_AI_TOOL_INPUT, context.tool_arguments)
        else:
            span.set_data(SPANDATA.GEN_AI_TOOL_INPUT, context.tool_arguments)

    async def on_tool_end(
        self,
        context: "ToolContext[TContext]",
        agent: "Agent[TContext]",
        tool: "Tool",
        result: "object",
    ) -> "None":
        if not isinstance(tool, FunctionTool):
            return

        span = getattr(context, "sentry_tool_span", None)
        if span is not None:
            del context.sentry_tool_span
            update_execute_tool_span(span, agent, tool, result)
            span.__exit__(None, None, None)


def _patch_run_hooks(hooks: "RunHooks[TContext]") -> None:
    is_already_patched = getattr(hooks, "_sentry_is_patched", False)
    if is_already_patched:
        return

    original_on_tool_start = hooks.on_tool_start
    original_on_tool_end = hooks.on_tool_end

    sentry_hooks = _SentryRunHooks()  # type: ignore[var-annotated]

    @wraps(original_on_tool_start)
    async def on_tool_start(
        context: "ToolContext[TContext]", agent: "Agent[TContext]", tool: "Tool"
    ) -> "None":
        with capture_internal_exceptions():
            await sentry_hooks.on_tool_start(context, agent, tool)
        await original_on_tool_start(context, agent, tool)

    @wraps(original_on_tool_end)
    async def on_tool_end(
        context: "ToolContext[TContext]",
        agent: "Agent[TContext]",
        tool: "Tool",
        result: "object",
    ) -> "None":
        with capture_internal_exceptions():
            await sentry_hooks.on_tool_end(context, agent, tool, result)
        await original_on_tool_end(context, agent, tool, result)

    hooks._sentry_is_patched = True
    hooks.on_tool_start = on_tool_start
    hooks.on_tool_end = on_tool_end


def _create_run_wrapper(
    original_func: "Callable[..., Any]", use_tool_hooks: "bool"
) -> "Callable[..., Any]":
    """
    Wraps the agents.Runner.run methods to
    - create and manage a root span for the agent workflow runs.
    - end the agent invocation span if an `AgentsException` is raised in `run()`.

    Note agents.Runner.run_sync() is a wrapper around agents.Runner.run(),
    so it does not need to be wrapped separately.
    """

    @wraps(original_func)
    async def wrapper(*args: "Any", **kwargs: "Any") -> "Any":
        if use_tool_hooks:
            hooks = kwargs.get("hooks")
            if hooks is not None:
                _patch_run_hooks(hooks=hooks)
            else:
                kwargs["hooks"] = _SentryRunHooks()

        # Isolate each workflow so that when agents are run in asyncio tasks they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            # Clone agent because agent invocation spans are attached per run.
            if "starting_agent" in kwargs:
                agent = kwargs["starting_agent"].clone()
            else:
                agent = args[0].clone()

            with agent_workflow_span(agent) as workflow_span:
                # Set conversation ID on workflow span early so it's captured even on errors
                conversation_id = kwargs.get("conversation_id")
                if conversation_id:
                    agent._sentry_conversation_id = conversation_id

                    if isinstance(workflow_span, StreamedSpan):
                        workflow_span.set_attribute(
                            SPANDATA.GEN_AI_CONVERSATION_ID, conversation_id
                        )
                    else:
                        workflow_span.set_data(
                            SPANDATA.GEN_AI_CONVERSATION_ID, conversation_id
                        )

                if "starting_agent" in kwargs:
                    kwargs["starting_agent"] = agent
                else:
                    args = (agent, *args[1:])

                try:
                    run_result = await original_func(*args, **kwargs)
                except AgentsException as exc:
                    exc_info = sys.exc_info()
                    with capture_internal_exceptions():
                        _capture_exception(exc)

                        context_wrapper = getattr(exc.run_data, "context_wrapper", None)
                        if context_wrapper is not None:
                            invoke_agent_span = getattr(
                                context_wrapper, "_sentry_agent_span", None
                            )

                            if invoke_agent_span is not None and (
                                (
                                    isinstance(invoke_agent_span, StreamedSpan)
                                    and invoke_agent_span.end_timestamp is None
                                )
                                or (
                                    not isinstance(invoke_agent_span, StreamedSpan)
                                    and invoke_agent_span.timestamp is None
                                )
                            ):
                                update_invoke_agent_span(
                                    span=invoke_agent_span,
                                    context=context_wrapper,
                                    agent=agent,
                                )

                                invoke_agent_span.__exit__(*exc_info)
                                delattr(context_wrapper, "_sentry_agent_span")
                    reraise(*exc_info)
                except Exception as exc:
                    exc_info = sys.exc_info()
                    with capture_internal_exceptions():
                        # Invoke agent span is not finished in this case.
                        # This is much less likely to occur than other cases because
                        # AgentRunner.run() is "just" a while loop around _run_single_turn.
                        _capture_exception(exc)
                    reraise(*exc_info)

                invoke_agent_span = getattr(
                    run_result.context_wrapper, "_sentry_agent_span", None
                )
                if not invoke_agent_span:
                    return run_result

                update_invoke_agent_span(
                    span=invoke_agent_span,
                    context=run_result.context_wrapper,
                    agent=agent,
                )

                invoke_agent_span.__exit__(None, None, None)
                delattr(run_result.context_wrapper, "_sentry_agent_span")
                return run_result

    return wrapper


def _create_run_streamed_wrapper(
    original_func: "Callable[..., Any]", use_tool_hooks: "bool"
) -> "Callable[..., Any]":
    """
    Wraps the agents.Runner.run_streamed method to
    - create a root span for streaming agent workflow runs.
    - end the workflow span if and only if the response stream is consumed or cancelled.

    Unlike run(), run_streamed() returns immediately with a RunResultStreaming object
    while execution continues in a background task. The workflow span must stay open
    throughout the streaming operation and close when streaming completes or is abandoned.

    Note: We don't use isolation_scope() here because it uses context variables that
    cannot span async boundaries (the __enter__ and __exit__ would be called from
    different async contexts, causing ValueError).
    """

    @wraps(original_func)
    def wrapper(*args: "Any", **kwargs: "Any") -> "Any":
        # Clone agent because agent invocation spans are attached per run.
        if "starting_agent" in kwargs:
            agent = kwargs["starting_agent"].clone()
        else:
            agent = args[0].clone()

        # Capture conversation_id from kwargs if provided
        conversation_id = kwargs.get("conversation_id")
        if conversation_id:
            agent._sentry_conversation_id = conversation_id

        # Start workflow span immediately (before run_streamed returns)
        workflow_span = agent_workflow_span(agent)
        workflow_span.__enter__()

        # Set conversation ID on workflow span early so it's captured even on errors
        if conversation_id:
            if isinstance(workflow_span, StreamedSpan):
                workflow_span.set_attribute(
                    SPANDATA.GEN_AI_CONVERSATION_ID, conversation_id
                )
            else:
                workflow_span.set_data(SPANDATA.GEN_AI_CONVERSATION_ID, conversation_id)

        # Store span on agent for cleanup
        agent._sentry_workflow_span = workflow_span

        if "starting_agent" in kwargs:
            kwargs["starting_agent"] = agent
        else:
            args = (agent, *args[1:])

        if use_tool_hooks:
            sentry_hooks = _SentryRunHooks()  # type: ignore[var-annotated]
            hooks = kwargs.get("hooks")
            if hooks is not None:
                _patch_run_hooks(hooks=hooks)
            else:
                kwargs["hooks"] = sentry_hooks

        try:
            # Call original function to get RunResultStreaming
            run_result = original_func(*args, **kwargs)
        except Exception as exc:
            # If run_streamed itself fails (not the background task), clean up immediately
            workflow_span.__exit__(*sys.exc_info())
            _capture_exception(exc)
            raise

        def _close_workflow_span() -> None:
            if hasattr(agent, "_sentry_workflow_span"):
                workflow_span.__exit__(*sys.exc_info())
                delattr(agent, "_sentry_workflow_span")

        if hasattr(run_result, "stream_events"):
            original_stream_events = run_result.stream_events

            @wraps(original_stream_events)
            async def wrapped_stream_events(
                *stream_args: "Any", **stream_kwargs: "Any"
            ) -> "AsyncIterator[Any]":
                try:
                    async for event in original_stream_events(
                        *stream_args, **stream_kwargs
                    ):
                        yield event
                finally:
                    _close_workflow_span()

            run_result.stream_events = wrapped_stream_events

        if hasattr(run_result, "cancel"):
            original_cancel = run_result.cancel

            @wraps(original_cancel)
            def wrapped_cancel(*cancel_args: "Any", **cancel_kwargs: "Any") -> "Any":
                try:
                    return original_cancel(*cancel_args, **cancel_kwargs)
                finally:
                    _close_workflow_span()

            run_result.cancel = wrapped_cancel

        return run_result

    return wrapper

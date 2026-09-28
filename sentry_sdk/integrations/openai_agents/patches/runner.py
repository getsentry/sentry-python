import sys
from functools import wraps

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    has_data_collection_enabled,
    reraise,
)

from ..spans import (
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
    from typing import Any, Callable

    from agents import Agent, Tool, ToolContext


TContext = TypeVar("TContext")


class _SentryRunHooks(RunHooks[TContext]):
    """
    Responsible for creating and managing Execute Tool spans. These spans are
    stored on the ToolContext reference that is shared between `on_tool_start()`
    and `on_tool_end()`
    """

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
        context._sentry_execute_tool_span = span

        client = sentry_sdk.get_client()
        if has_data_collection_enabled(client.options):
            if not client.options["data_collection"]["gen_ai"]["inputs"]:
                return
        elif not should_send_default_pii():
            return

        span.set_attribute(SPANDATA.GEN_AI_TOOL_INPUT, context.tool_arguments)

    async def on_tool_end(
        self,
        context: "ToolContext[TContext]",
        agent: "Agent[TContext]",
        tool: "Tool",
        result: "object",
    ) -> "None":
        if not isinstance(tool, FunctionTool):
            return

        span = getattr(context, "_sentry_execute_tool_span", None)
        if span is not None:
            del context._sentry_execute_tool_span
            update_execute_tool_span(span, agent, tool, result)
            span.__exit__(None, None, None)


def _patch_run_hooks(hooks: "RunHooks[TContext]") -> None:
    """
    Patch a RunHooks instance. This is used when the user have themselves provided
    a RunHooks instance, as only one instance can be passed to `AgentRunner.run()`
    and `AgentRunner.run_streamed()` functions.
    """
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

    hooks._sentry_is_patched = True  # type: ignore[attr-defined]
    hooks.on_tool_start = on_tool_start  # type: ignore[method-assign]
    hooks.on_tool_end = on_tool_end  # type: ignore[method-assign]


def _create_run_wrapper(
    original_func: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """
    Wraps the agents.Runner.run methods to
    - end the agent invocation span if an `AgentsException` is raised in `run()`.

    Note agents.Runner.run_sync() is a wrapper around agents.Runner.run(),
    so it does not need to be wrapped separately.
    """

    @wraps(original_func)
    async def wrapper(*args: "Any", **kwargs: "Any") -> "Any":
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

            # Set conversation ID on workflow span early so it's captured even on errors
            conversation_id = kwargs.get("conversation_id")
            if conversation_id:
                agent._sentry_conversation_id = conversation_id

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

                        if (
                            invoke_agent_span is not None
                            and invoke_agent_span.end_timestamp is None
                        ):
                            update_invoke_agent_span(
                                span=invoke_agent_span,
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
                agent=agent,
            )

            invoke_agent_span.__exit__(None, None, None)
            delattr(run_result.context_wrapper, "_sentry_agent_span")
            return run_result

    return wrapper


def _create_run_streamed_wrapper(
    original_func: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """
    Wraps the agents.Runner.run_streamed method to inject run hooks.
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

        if "starting_agent" in kwargs:
            kwargs["starting_agent"] = agent
        else:
            args = (agent, *args[1:])

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
            _capture_exception(exc)
            raise

        return run_result

    return wrapper

import sys
from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from ..spans import agent_workflow_span, end_invoke_agent_span
from ..utils import _capture_exception, _record_exception_on_span

try:
    from agents.exceptions import AgentsException
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


def _create_run_wrapper(original_func: "Callable[..., Any]") -> "Callable[..., Any]":
    """
    Wraps the agents.Runner.run methods to create a root span for the agent workflow runs.

    Note agents.Runner.run_sync() is a wrapper around agents.Runner.run(),
    so it does not need to be wrapped separately.
    """

    @wraps(original_func)
    async def wrapper(*args: "Any", **kwargs: "Any") -> "Any":
        # Isolate each workflow so that when agents are run in asyncio tasks they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            # Clone agent because agent invocation spans are attached per run.
            agent = args[0].clone()
            with agent_workflow_span(agent):
                args = (agent, *args[1:])
                try:
                    run_result = await original_func(*args, **kwargs)
                except AgentsException as exc:
                    _capture_exception(exc)

                    context_wrapper = getattr(exc.run_data, "context_wrapper", None)
                    if context_wrapper is not None:
                        invoke_agent_span = getattr(
                            context_wrapper, "_sentry_agent_span", None
                        )

                        if (
                            invoke_agent_span is not None
                            and invoke_agent_span.timestamp is None
                        ):
                            _record_exception_on_span(invoke_agent_span, exc)
                            end_invoke_agent_span(context_wrapper, agent)

                    raise exc from None
                except Exception as exc:
                    # Invoke agent span is not finished in this case.
                    # This is much less likely to occur than other cases because
                    # AgentRunner.run() is "just" a while loop around _run_single_turn.
                    _capture_exception(exc)
                    raise exc from None

                end_invoke_agent_span(run_result.context_wrapper, agent)
                return run_result

    return wrapper


def _create_run_streamed_wrapper(
    original_func: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """
    Wraps the agents.Runner.run_streamed method to create a root span for streaming agent workflow runs.

    Unlike run(), run_streamed() returns immediately with a RunResultStreaming object
    while execution continues in a background task. The workflow span must stay open
    throughout the streaming operation and close when streaming completes or is abandoned.
    """

    @wraps(original_func)
    def wrapper(*args: "Any", **kwargs: "Any") -> "Any":
        # Isolate each workflow so that when agents are run in asyncio tasks they
        # don't touch each other's scopes
        isolation_scope = sentry_sdk.isolation_scope()
        isolation_scope.__enter__()

        # Clone agent because agent invocation spans are attached per run.
        agent = args[0].clone()

        # Start workflow span immediately (before run_streamed returns)
        workflow_span = agent_workflow_span(agent)
        workflow_span.__enter__()

        # Store span and scope on agent for cleanup
        agent._sentry_workflow_span = workflow_span
        agent._sentry_isolation_scope = isolation_scope

        args = (agent, *args[1:])

        try:
            # Call original function to get RunResultStreaming
            run_result = original_func(*args, **kwargs)
        except Exception as exc:
            # If run_streamed itself fails (not the background task), clean up immediately
            workflow_span.__exit__(*sys.exc_info())
            isolation_scope.__exit__(None, None, None)
            _capture_exception(exc)
            raise exc from None

        # Wrap the result to ensure cleanup when streaming completes
        original_aclose = getattr(run_result, "aclose", None)

        async def wrapped_aclose() -> None:
            """Close streaming result and clean up Sentry spans"""
            try:
                if original_aclose is not None:
                    await original_aclose()
            finally:
                # End any remaining agent span
                if hasattr(run_result, "context_wrapper"):
                    end_invoke_agent_span(run_result.context_wrapper, agent)

                # End workflow span
                if hasattr(agent, "_sentry_workflow_span"):
                    workflow_span.__exit__(None, None, None)
                    delattr(agent, "_sentry_workflow_span")

                # Exit isolation scope
                if hasattr(agent, "_sentry_isolation_scope"):
                    isolation_scope.__exit__(None, None, None)
                    delattr(agent, "_sentry_isolation_scope")

        run_result.aclose = wrapped_aclose

        return run_result

    return wrapper

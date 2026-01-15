import sys
from functools import wraps

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.utils import capture_internal_exceptions, reraise

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
                                and invoke_agent_span.timestamp is None
                            ):
                                _record_exception_on_span(invoke_agent_span, exc)
                                end_invoke_agent_span(context_wrapper, agent)
                    reraise(*exc_info)
                except Exception as exc:
                    exc_info = sys.exc_info()
                    with capture_internal_exceptions():
                        # Invoke agent span is not finished in this case.
                        # This is much less likely to occur than other cases because
                        # AgentRunner.run() is "just" a while loop around _run_single_turn.
                        _capture_exception(exc)
                    reraise(*exc_info)

                end_invoke_agent_span(run_result.context_wrapper, agent)
                return run_result

    return wrapper

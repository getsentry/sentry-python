from functools import wraps

import sentry_sdk

from ..spans import agent_workflow_span
from ..utils import _capture_exception

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


def _create_run_wrapper(original_func):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps the agents.Runner.run methods to create a root span for the agent workflow runs.

    Note agents.Runner.run_sync() is a wrapper around agents.Runner.run(),
    so it does not need to be wrapped separately.
    """

    @wraps(original_func)
    async def wrapper(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        # Isolate each workflow so that when agents are run in asyncio tasks they
        # don't touch each other's scopes
        with sentry_sdk.isolation_scope():
            agent = args[0]
            with agent_workflow_span(agent):
                run_result = await original_func(*args, **kwargs)

                invoke_agent_span = getattr(
                    run_result.context_wrapper, "_sentry_agent_span", None
                )

                if (
                    invoke_agent_span is not None
                    and invoke_agent_span.timestamp is None
                ):
                    invoke_agent_span.__exit__(None, None, None)

                return run_result

    return wrapper

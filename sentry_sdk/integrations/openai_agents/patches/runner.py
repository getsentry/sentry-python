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
        agent = args[0]
        with agent_workflow_span(agent):
            result = None
            try:
                result = await original_func(*args, **kwargs)
                return result
            except Exception as exc:
                _capture_exception(exc)

                # It could be that there is a "invoke agent" span still open
                current_span = sentry_sdk.get_current_span()
                if current_span is not None and current_span.timestamp is None:
                    current_span.__exit__(None, None, None)

                raise exc from None

    return wrapper

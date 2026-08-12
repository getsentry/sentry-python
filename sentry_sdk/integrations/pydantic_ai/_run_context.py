"""Run-scoped state shared between the agent wrappers and the model/tool
instrumentation.

One agent run corresponds to one AgentRun on the contextvar stack. The stack
makes nested agent calls re-entrant safe, and the context manager guarantees
push/pop pairing.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Iterator, Optional


@dataclass
class AgentRun:
    """State for one in-flight agent run."""

    agent: "Any"
    is_streaming: bool = False


_agent_run_stack: "ContextVar[tuple[AgentRun, ...]]" = ContextVar(
    "pydantic_ai_agent_run_stack", default=()
)


def current_agent_run() -> "Optional[AgentRun]":
    stack = _agent_run_stack.get()
    return stack[-1] if stack else None


def get_current_agent() -> "Any":
    run = current_agent_run()
    return run.agent if run is not None else None


def get_is_streaming() -> bool:
    run = current_agent_run()
    return run.is_streaming if run is not None else False


@contextmanager
def agent_run_scope(agent: "Any", is_streaming: bool = False) -> "Iterator[AgentRun]":
    """Track an agent run on the contextvar stack for the duration of the
    with block."""
    run = AgentRun(agent=agent, is_streaming=is_streaming)
    token = _agent_run_stack.set(_agent_run_stack.get() + (run,))
    try:
        yield run
    finally:
        try:
            _agent_run_stack.reset(token)
        except (LookupError, ValueError):
            # A streaming run's context manager can be exited in a different
            # asyncio task (and therefore a different Context) than it was
            # entered in, in which case the token cannot be reset. The stack
            # entry only lives in the entering task's context copy, so there
            # is nothing to clean up.
            pass

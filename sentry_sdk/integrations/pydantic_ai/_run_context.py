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
    with block.

    On exit, exactly this run is removed from the stack (by identity, not a
    token reset), so streaming runs that exit out of LIFO order or in a
    different asyncio task never erase other still-active runs.
    """
    run = AgentRun(agent=agent, is_streaming=is_streaming)
    _agent_run_stack.set(_agent_run_stack.get() + (run,))
    try:
        yield run
    finally:
        stack = _agent_run_stack.get()
        new_stack = tuple(r for r in stack if r is not run)
        if len(new_stack) != len(stack):
            _agent_run_stack.set(new_stack)

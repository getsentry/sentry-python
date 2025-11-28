"""
Context variables for passing data between nested calls in the OpenAI Agents integration.
"""

from contextvars import ContextVar

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Context variable to pass response model between nested calls (for gen_ai.chat spans)
_response_model_context = ContextVar("openai_agents_response_model", default=None)  # type: ContextVar[str | None]

# Context variable to store the last response model for invoke_agent spans
_invoke_agent_response_model_context = ContextVar(
    "openai_agents_invoke_agent_response_model", default=None
)  # type: ContextVar[str | None]

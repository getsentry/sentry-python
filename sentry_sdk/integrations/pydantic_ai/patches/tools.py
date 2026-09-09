from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable

from ..spans import execute_tool_span, update_execute_tool_span
from ..utils import get_current_agent

if TYPE_CHECKING:
    from typing import Any

try:
    try:
        from pydantic_ai.tool_manager import ToolManager
    except ImportError:
        from pydantic_ai._tool_manager import ToolManager  # type: ignore
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


def _patch_tool_execution() -> None:
    if hasattr(ToolManager, "execute_tool_call"):
        _patch_execute_tool_call()


def _patch_execute_tool_call() -> None:
    original_execute_tool_call = ToolManager.execute_tool_call

    @wraps(original_execute_tool_call)
    async def wrapped_execute_tool_call(
        self: "Any", validated: "Any", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        if not validated or not hasattr(validated, "call") or not validated.args_valid:
            return await original_execute_tool_call(self, validated, *args, **kwargs)

        # Extract tool info before calling original
        call = validated.call
        name = call.tool_name
        tool = self.tools.get(name) if self.tools else None
        selected_tool_definition = getattr(tool, "tool_def", None)

        # Get agent from contextvar
        agent = get_current_agent()

        if agent and tool:
            try:
                args_dict = call.args_as_dict()
            except Exception:
                args_dict = call.args if isinstance(call.args, dict) else {}

            # Create execute_tool span
            # Nesting is handled by isolation_scope() to ensure proper parent-child relationships
            with sentry_sdk.isolation_scope():
                with execute_tool_span(
                    name,
                    args_dict,
                    agent,
                    tool_definition=selected_tool_definition,
                ) as span:
                    result = await original_execute_tool_call(
                        self,
                        validated,
                        *args,
                        **kwargs,
                    )
                    update_execute_tool_span(span, result)
                    return result

        return await original_execute_tool_call(self, validated, *args, **kwargs)

    ToolManager.execute_tool_call = wrapped_execute_tool_call  # type: ignore[method-assign]

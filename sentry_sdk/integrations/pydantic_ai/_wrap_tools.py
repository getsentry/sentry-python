"""Instrumentation of tool execution.

All tool calls in pydantic-ai flow through one ToolManager method (named
execute_tool_call on newer versions, _call_tool on older ones — resolved in
_compat), regardless of toolset type (function, MCP, combined, wrapper, ...).
Patching there avoids patching multiple toolset classes and dealing with
signature mismatches from instrumented MCP servers.
"""

import sys
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.utils import capture_internal_exceptions, reraise

from ._compat import TOOL_CALL_METHOD, ToolManager, ToolRetryError
from ._extract import extract_tool_call_args
from ._run_context import get_current_agent
from ._spans import _capture_exception, execute_tool_span, update_execute_tool_span

if TYPE_CHECKING:
    from typing import Any


def _patch_tool_execution() -> None:
    if TOOL_CALL_METHOD is None:
        # No known tool-call method on this version; skip tool instrumentation
        # rather than crashing setup.
        return

    original_method = getattr(ToolManager, TOOL_CALL_METHOD)

    @wraps(original_method)
    async def wrapped_tool_call(
        self: "Any", first_arg: "Any", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        # execute_tool_call receives a validated wrapper holding the call;
        # the older _call_tool receives the call directly.
        if TOOL_CALL_METHOD == "execute_tool_call":
            if not first_arg or not hasattr(first_arg, "call"):
                return await original_method(self, first_arg, *args, **kwargs)
            call = first_arg.call
        else:
            call = first_arg

        name = call.tool_name
        tool = self.tools.get(name) if self.tools else None
        selected_tool_definition = getattr(tool, "tool_def", None)

        # Get agent from contextvar
        agent = get_current_agent()

        if not (agent and tool):
            # No span context - just call original
            return await original_method(self, first_arg, *args, **kwargs)

        args_dict = extract_tool_call_args(call)

        # Create execute_tool span
        # Nesting is handled by isolation_scope() to ensure proper parent-child relationships
        with sentry_sdk.isolation_scope():
            with execute_tool_span(
                name,
                args_dict,
                agent,
                tool_definition=selected_tool_definition,
            ) as span:
                try:
                    result = await original_method(self, first_arg, *args, **kwargs)
                    update_execute_tool_span(span, result)
                    return result
                except ToolRetryError as exc:
                    exc_info = sys.exc_info()
                    with capture_internal_exceptions():
                        from sentry_sdk.integrations.pydantic_ai import (
                            PydanticAIIntegration,
                        )

                        integration = sentry_sdk.get_client().get_integration(
                            PydanticAIIntegration
                        )
                        if (
                            integration is not None
                            and integration.handled_tool_call_exceptions
                        ):
                            _capture_exception(exc, handled=True)
                    reraise(*exc_info)

    setattr(ToolManager, TOOL_CALL_METHOD, wrapped_tool_call)

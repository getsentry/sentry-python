import sys
from contextlib import nullcontext
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.utils import capture_internal_exceptions, reraise

from ..spans import execute_tool_span, update_execute_tool_span
from ..utils import _capture_exception, get_current_agent

if TYPE_CHECKING:
    from typing import Any

try:
    try:
        from pydantic_ai.tool_manager import ToolManager
    except ImportError:
        from pydantic_ai._tool_manager import ToolManager  # type: ignore

    from pydantic_ai.exceptions import ToolRetryError
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
        if not validated or not hasattr(validated, "call"):
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
                with (
                    execute_tool_span(
                        name,
                        args_dict,
                        agent,
                        tool_definition=selected_tool_definition,
                    )
                    if validated.args_valid
                    else nullcontext()
                ) as span:
                    try:
                        result = await original_execute_tool_call(
                            self,
                            validated,
                            *args,
                            **kwargs,
                        )
                        if span is not None:
                            update_execute_tool_span(span, result)
                        return result
                    except ToolRetryError as exc:
                        exc_info = sys.exc_info()
                        with capture_internal_exceptions():
                            # Avoid circular import due to multi-file integration structure
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

        return await original_execute_tool_call(self, validated, *args, **kwargs)

    ToolManager.execute_tool_call = wrapped_execute_tool_call  # type: ignore[method-assign]

"""
Patches for mcp.server.lowlevel.Server class.
"""

import inspect
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.mcp import MCPIntegration
from sentry_sdk.utils import safe_serialize

if TYPE_CHECKING:
    from typing import Any, Callable


def _get_span_config(handler_type, handler_name):
    # type: (str, str) -> tuple[str, str, str, str]
    """
    Get span configuration based on handler type.

    Returns:
        Tuple of (op, span_data_key, span_name, mcp_method_name)
    """
    if handler_type == "tool":
        op = OP.MCP_TOOL
        span_data_key = SPANDATA.MCP_TOOL_NAME
        mcp_method_name = "tools/call"
    elif handler_type == "prompt":
        op = OP.MCP_PROMPT
        span_data_key = SPANDATA.MCP_PROMPT_NAME
        mcp_method_name = "prompts/get"
    else:  # resource
        op = OP.MCP_RESOURCE
        span_data_key = SPANDATA.MCP_RESOURCE_URI
        mcp_method_name = "resources/read"

    span_name = f"{handler_type} {handler_name}"
    return op, span_data_key, span_name, mcp_method_name


def _set_span_data(span, handler_name, span_data_key, mcp_method_name, kwargs):
    # type: (Any, str, str, str, dict[str, Any]) -> None
    """Set common span data for MCP handlers."""
    # Set handler identifier
    span.set_data(span_data_key, handler_name)
    span.set_data(SPANDATA.MCP_METHOD_NAME, mcp_method_name)

    # Set request arguments (excluding common request context objects)
    for k, v in kwargs.items():
        span.set_data(f"mcp.request.argument.{k}", safe_serialize(v))


def wrap_handler(original_handler, handler_type, handler_name):
    # type: (Callable[..., Any], str, str) -> Callable[..., Any]
    """
    Wraps a handler function to create spans and capture errors.

    Args:
        original_handler: The original handler function to wrap
        handler_type: Type of handler ('tool', 'prompt', or 'resource')
        handler_name: Name or identifier of the handler

    Returns:
        Wrapped handler function
    """
    op, span_data_key, span_name, mcp_method_name = _get_span_config(
        handler_type, handler_name
    )

    if inspect.iscoroutinefunction(original_handler):

        @wraps(original_handler)
        async def async_wrapper(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            with get_start_span_function()(
                op=op,
                name=span_name,
                origin=MCPIntegration.origin,
            ) as span:
                _set_span_data(
                    span, handler_name, span_data_key, mcp_method_name, kwargs
                )
                try:
                    return await original_handler(*args, **kwargs)
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    raise

        return async_wrapper
    else:

        @wraps(original_handler)
        def sync_wrapper(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            with get_start_span_function()(
                op=op,
                name=span_name,
                origin=MCPIntegration.origin,
            ) as span:
                _set_span_data(
                    span, handler_name, span_data_key, mcp_method_name, kwargs
                )
                try:
                    return original_handler(*args, **kwargs)
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    raise

        return sync_wrapper


def patch_lowlevel_server():
    # type: () -> None
    """
    Patches the mcp.server.lowlevel.Server class to instrument handler execution.
    """
    from mcp.server.lowlevel import Server

    # Patch call_tool decorator
    original_call_tool = Server.call_tool

    def patched_call_tool(self, **kwargs):
        # type: (Server, **Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            # Extract tool name from the function
            tool_name = getattr(func, "__name__", "unknown")
            wrapped_func = wrap_handler(func, "tool", tool_name)

            # Call the original decorator with the wrapped function
            return original_call_tool(self, **kwargs)(wrapped_func)

        return decorator

    Server.call_tool = patched_call_tool  # type: ignore

    # Patch get_prompt decorator
    original_get_prompt = Server.get_prompt

    def patched_get_prompt(self):
        # type: (Server) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            # The handler receives (name, arguments) as parameters
            # We need to extract the name from the call
            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def async_name_wrapper(name, arguments):
                    # type: (str, Any) -> Any
                    wrapped = wrap_handler(func, "prompt", name)
                    return await wrapped(name, arguments)

                return original_get_prompt(self)(async_name_wrapper)
            else:

                @wraps(func)
                def sync_name_wrapper(name, arguments):
                    # type: (str, Any) -> Any
                    wrapped = wrap_handler(func, "prompt", name)
                    return wrapped(name, arguments)

                return original_get_prompt(self)(sync_name_wrapper)

        return decorator

    Server.get_prompt = patched_get_prompt  # type: ignore

    # Patch read_resource decorator
    original_read_resource = Server.read_resource

    def patched_read_resource(self):
        # type: (Server) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            # The handler receives a URI as a parameter
            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def async_uri_wrapper(uri):
                    # type: (Any) -> Any
                    uri_str = str(uri) if uri else "unknown"
                    wrapped = wrap_handler(func, "resource", uri_str)
                    return await wrapped(uri)

                return original_read_resource(self)(async_uri_wrapper)
            else:

                @wraps(func)
                def sync_uri_wrapper(uri):
                    # type: (Any) -> Any
                    uri_str = str(uri) if uri else "unknown"
                    wrapped = wrap_handler(func, "resource", uri_str)
                    return wrapped(uri)

                return original_read_resource(self)(sync_uri_wrapper)

        return decorator

    Server.read_resource = patched_read_resource  # type: ignore

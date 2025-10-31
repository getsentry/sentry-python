"""
Sentry integration for MCP (Model Context Protocol) servers.

This integration instruments MCP servers to create spans for tool, prompt,
and resource handler execution, and captures errors that occur during execution.

Supports the low-level `mcp.server.lowlevel.Server` API.
"""

import inspect
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.utils import safe_serialize
from sentry_sdk.scope import should_send_default_pii

try:
    from mcp.server.lowlevel import Server  # type: ignore[import-not-found]
    from mcp.server.lowlevel.server import request_ctx  # type: ignore[import-not-found]
except ImportError:
    raise DidNotEnable("MCP SDK not installed")


if TYPE_CHECKING:
    from typing import Any, Callable, Optional


class MCPIntegration(Integration):
    identifier = "mcp"
    origin = "auto.ai.mcp"

    def __init__(self, include_prompts=True):
        # type: (bool) -> None
        """
        Initialize the MCP integration.

        Args:
            include_prompts: Whether to include prompts (tool results and prompt content)
                             in span data. Requires send_default_pii=True. Default is True.
        """
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once():
        # type: () -> None
        """
        Patches MCP server classes to instrument handler execution.
        """
        _patch_lowlevel_server()


def _get_request_context_data():
    # type: () -> tuple[Optional[str], Optional[str], str]
    """
    Extract request ID, session ID, and transport type from the MCP request context.

    Returns:
        Tuple of (request_id, session_id, transport).
        - request_id: May be None if not available
        - session_id: May be None if not available
        - transport: "tcp" for HTTP-based, "pipe" for stdio
    """
    request_id = None  # type: Optional[str]
    session_id = None  # type: Optional[str]
    transport = "pipe"  # type: str

    try:
        ctx = request_ctx.get()

        if ctx is not None:
            request_id = ctx.request_id
            if hasattr(ctx, "request") and ctx.request is not None:
                transport = "tcp"
                request = ctx.request
                if hasattr(request, "headers"):
                    session_id = request.headers.get("mcp-session-id")

    except LookupError:
        # No request context available - default to pipe
        pass

    return request_id, session_id, transport


def _get_span_config(handler_type, item_name):
    # type: (str, str) -> tuple[str, str, str, Optional[str]]
    """
    Get span configuration based on handler type.

    Returns:
        Tuple of (span_data_key, span_name, mcp_method_name, result_data_key)
        Note: result_data_key is None for resources
    """
    if handler_type == "tool":
        span_data_key = SPANDATA.MCP_TOOL_NAME
        mcp_method_name = "tools/call"
        result_data_key = SPANDATA.MCP_TOOL_RESULT_CONTENT
    elif handler_type == "prompt":
        span_data_key = SPANDATA.MCP_PROMPT_NAME
        mcp_method_name = "prompts/get"
        result_data_key = SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT
    else:  # resource
        span_data_key = SPANDATA.MCP_RESOURCE_URI
        mcp_method_name = "resources/read"
        result_data_key = None  # Resources don't capture result content

    span_name = f"{mcp_method_name} {item_name}"
    return span_data_key, span_name, mcp_method_name, result_data_key


def _set_span_input_data(
    span,
    handler_name,
    span_data_key,
    mcp_method_name,
    arguments,
    request_id,
    session_id,
    transport,
):
    # type: (Any, str, str, str, dict[str, Any], Optional[str], Optional[str], str) -> None
    """Set input span data for MCP handlers."""
    # Set handler identifier
    span.set_data(span_data_key, handler_name)
    span.set_data(SPANDATA.MCP_METHOD_NAME, mcp_method_name)

    # Set transport type
    span.set_data(SPANDATA.MCP_TRANSPORT, transport)

    # Set request_id if provided
    if request_id:
        span.set_data(SPANDATA.MCP_REQUEST_ID, request_id)

    # Set session_id if provided
    if session_id:
        span.set_data(SPANDATA.MCP_SESSION_ID, session_id)

    # Set request arguments (excluding common request context objects)
    for k, v in arguments.items():
        span.set_data(f"mcp.request.argument.{k}", safe_serialize(v))


def _extract_tool_result_content(result):
    # type: (Any) -> Any
    """
    Extract meaningful content from MCP tool result.

    Tool handlers can return:
    - tuple (UnstructuredContent, StructuredContent): Return the structured content (dict)
    - dict (StructuredContent): Return as-is
    - Iterable (UnstructuredContent): Extract text from content blocks
    """
    if result is None:
        return None

    # Handle CombinationContent: tuple of (UnstructuredContent, StructuredContent)
    if isinstance(result, tuple) and len(result) == 2:
        # Return the structured content (2nd element)
        return result[1]

    # Handle StructuredContent: dict
    if isinstance(result, dict):
        return result

    # Handle UnstructuredContent: iterable of ContentBlock objects
    # Try to extract text content
    if hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict)):
        texts = []
        try:
            for item in result:
                # Try to get text attribute from ContentBlock objects
                if hasattr(item, "text"):
                    texts.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
        except Exception:
            # If extraction fails, return the original
            return result
        return " ".join(texts) if texts else result

    return result


def _set_span_output_data(span, result, result_data_key, handler_type):
    # type: (Any, Any, Optional[str], str) -> None
    """Set output span data for MCP handlers."""
    if result is None:
        return

    # Get integration to check PII settings
    integration = sentry_sdk.get_client().get_integration(MCPIntegration)
    if integration is None:
        return

    # Check if we should include sensitive data
    should_include_data = should_send_default_pii() and integration.include_prompts

    # For tools, extract the meaningful content
    if handler_type == "tool":
        extracted = _extract_tool_result_content(result)
        if extracted is not None and should_include_data:
            span.set_data(result_data_key, safe_serialize(extracted))
            # Set content count if result is a dict
            if isinstance(extracted, dict):
                span.set_data(SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT, len(extracted))
    elif handler_type == "prompt":
        # For prompts, count messages and set role/content only for single-message prompts
        try:
            messages = None  # type: Optional[list[str]]
            message_count = 0

            # Check if result has messages attribute (GetPromptResult)
            if hasattr(result, "messages") and result.messages:
                messages = result.messages
                message_count = len(messages)
            # Also check if result is a dict with messages
            elif isinstance(result, dict) and result.get("messages"):
                messages = result["messages"]
                message_count = len(messages)

            # Always set message count if we found messages
            if message_count > 0:
                span.set_data(SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT, message_count)

            # Only set role and content for single-message prompts if PII is allowed
            if message_count == 1 and should_include_data and messages:
                first_message = messages[0]
                # Extract role
                role = None
                if hasattr(first_message, "role"):
                    role = first_message.role
                elif isinstance(first_message, dict) and "role" in first_message:
                    role = first_message["role"]

                if role:
                    span.set_data(SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE, role)

                # Extract content text
                content_text = None
                if hasattr(first_message, "content"):
                    msg_content = first_message.content
                    # Content can be a TextContent object or similar
                    if hasattr(msg_content, "text"):
                        content_text = msg_content.text
                    elif isinstance(msg_content, dict) and "text" in msg_content:
                        content_text = msg_content["text"]
                    elif isinstance(msg_content, str):
                        content_text = msg_content
                elif isinstance(first_message, dict) and "content" in first_message:
                    msg_content = first_message["content"]
                    if isinstance(msg_content, dict) and "text" in msg_content:
                        content_text = msg_content["text"]
                    elif isinstance(msg_content, str):
                        content_text = msg_content

                if content_text:
                    span.set_data(result_data_key, content_text)
        except Exception:
            # Silently ignore if we can't extract message info
            pass
    # Resources don't capture result content (result_data_key is None)


# Handler data preparation and wrapping


def _prepare_handler_data(handler_type, original_args):
    # type: (str, tuple[Any, ...]) -> tuple[str, dict[str, Any], str, str, str, Optional[str]]
    """
    Prepare common handler data for both async and sync wrappers.

    Returns:
        Tuple of (handler_name, arguments, span_data_key, span_name, mcp_method_name, result_data_key)
    """
    # Extract handler-specific data based on handler type
    if handler_type == "tool":
        handler_name = original_args[0]  # tool_name
        arguments = original_args[1] if len(original_args) > 1 else {}
    elif handler_type == "prompt":
        handler_name = original_args[0]  # name
        arguments = original_args[1] if len(original_args) > 1 else {}
        # Include name in arguments dict for span data
        arguments = {"name": handler_name, **(arguments or {})}
    else:  # resource
        uri = original_args[0]
        handler_name = str(uri) if uri else "unknown"
        arguments = {}

    # Get span configuration
    span_data_key, span_name, mcp_method_name, result_data_key = _get_span_config(
        handler_type, handler_name
    )

    return (
        handler_name,
        arguments,
        span_data_key,
        span_name,
        mcp_method_name,
        result_data_key,
    )


async def _async_handler_wrapper(handler_type, func, original_args):
    # type: (str, Callable[..., Any], tuple[Any, ...]) -> Any
    """
    Async wrapper for MCP handlers.

    Args:
        handler_type: "tool", "prompt", or "resource"
        func: The async handler function to wrap
        original_args: Original arguments passed to the handler
    """
    (
        handler_name,
        arguments,
        span_data_key,
        span_name,
        mcp_method_name,
        result_data_key,
    ) = _prepare_handler_data(handler_type, original_args)

    # Start span and execute
    with get_start_span_function()(
        op=OP.MCP_SERVER,
        name=span_name,
        origin=MCPIntegration.origin,
    ) as span:
        # Get request ID, session ID, and transport from context
        request_id, session_id, transport = _get_request_context_data()

        # Set input span data
        _set_span_input_data(
            span,
            handler_name,
            span_data_key,
            mcp_method_name,
            arguments,
            request_id,
            session_id,
            transport,
        )

        # For resources, extract and set protocol
        if handler_type == "resource":
            uri = original_args[0]
            protocol = None
            if hasattr(uri, "scheme"):
                protocol = uri.scheme
            elif handler_name and "://" in handler_name:
                protocol = handler_name.split("://")[0]
            if protocol:
                span.set_data(SPANDATA.MCP_RESOURCE_PROTOCOL, protocol)

        try:
            # Execute the async handler
            result = await func(*original_args)
        except Exception as e:
            # Set error flag for tools
            if handler_type == "tool":
                span.set_data(SPANDATA.MCP_TOOL_RESULT_IS_ERROR, True)
            sentry_sdk.capture_exception(e)
            raise

        _set_span_output_data(span, result, result_data_key, handler_type)
        return result


def _sync_handler_wrapper(handler_type, func, original_args):
    # type: (str, Callable[..., Any], tuple[Any, ...]) -> Any
    """
    Sync wrapper for MCP handlers.

    Args:
        handler_type: "tool", "prompt", or "resource"
        func: The sync handler function to wrap
        original_args: Original arguments passed to the handler
    """
    (
        handler_name,
        arguments,
        span_data_key,
        span_name,
        mcp_method_name,
        result_data_key,
    ) = _prepare_handler_data(handler_type, original_args)

    # Start span and execute
    with get_start_span_function()(
        op=OP.MCP_SERVER,
        name=span_name,
        origin=MCPIntegration.origin,
    ) as span:
        # Get request ID, session ID, and transport from context
        request_id, session_id, transport = _get_request_context_data()

        # Set input span data
        _set_span_input_data(
            span,
            handler_name,
            span_data_key,
            mcp_method_name,
            arguments,
            request_id,
            session_id,
            transport,
        )

        # For resources, extract and set protocol
        if handler_type == "resource":
            uri = original_args[0]
            protocol = None
            if hasattr(uri, "scheme"):
                protocol = uri.scheme
            elif handler_name and "://" in handler_name:
                protocol = handler_name.split("://")[0]
            if protocol:
                span.set_data(SPANDATA.MCP_RESOURCE_PROTOCOL, protocol)

        try:
            # Execute the sync handler
            result = func(*original_args)
        except Exception as e:
            # Set error flag for tools
            if handler_type == "tool":
                span.set_data(SPANDATA.MCP_TOOL_RESULT_IS_ERROR, True)
            sentry_sdk.capture_exception(e)
            raise

        _set_span_output_data(span, result, result_data_key, handler_type)
        return result


def _create_instrumented_handler(handler_type, func):
    # type: (str, Callable[..., Any]) -> Callable[..., Any]
    """
    Create an instrumented version of a handler function (async or sync).

    This function wraps the user's handler with a runtime wrapper that will create
    Sentry spans and capture metrics when the handler is actually called.

    The wrapper preserves the async/sync nature of the original function, which is
    critical for Python's async/await to work correctly.

    Args:
        handler_type: "tool", "prompt", or "resource" - determines span configuration
        func: The handler function to instrument (async or sync)

    Returns:
        A wrapped version of func that creates Sentry spans on execution
    """
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args):
            # type: (*Any) -> Any
            return await _async_handler_wrapper(handler_type, func, args)

        return async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args):
            # type: (*Any) -> Any
            return _sync_handler_wrapper(handler_type, func, args)

        return sync_wrapper


def _create_instrumented_decorator(
    original_decorator, handler_type, *decorator_args, **decorator_kwargs
):
    # type: (Callable[..., Any], str, *Any, **Any) -> Callable[..., Any]
    """
    Create an instrumented version of an MCP decorator.

    This function intercepts MCP decorators (like @server.call_tool()) and injects
    Sentry instrumentation into the handler registration flow. The returned decorator
    will:
    1. Receive the user's handler function
    2. Wrap it with instrumentation via _create_instrumented_handler
    3. Pass the instrumented version to the original MCP decorator

    This ensures that when the handler is called at runtime, it's already wrapped
    with Sentry spans and metrics collection.

    Args:
        original_decorator: The original MCP decorator method (e.g., Server.call_tool)
        handler_type: "tool", "prompt", or "resource" - determines span configuration
        decorator_args: Positional arguments to pass to the original decorator (e.g., self)
        decorator_kwargs: Keyword arguments to pass to the original decorator

    Returns:
        A decorator function that instruments handlers before registering them
    """

    def instrumented_decorator(func):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        # First wrap the handler with instrumentation
        instrumented_func = _create_instrumented_handler(handler_type, func)
        # Then register it with the original MCP decorator
        return original_decorator(*decorator_args, **decorator_kwargs)(
            instrumented_func
        )

    return instrumented_decorator


def _patch_lowlevel_server():
    # type: () -> None
    """
    Patches the mcp.server.lowlevel.Server class to instrument handler execution.
    """
    # Patch call_tool decorator
    original_call_tool = Server.call_tool

    def patched_call_tool(self, **kwargs):
        # type: (Server, **Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        """Patched version of Server.call_tool that adds Sentry instrumentation."""
        return lambda func: _create_instrumented_decorator(
            original_call_tool, "tool", self, **kwargs
        )(func)

    Server.call_tool = patched_call_tool

    # Patch get_prompt decorator
    original_get_prompt = Server.get_prompt

    def patched_get_prompt(self):
        # type: (Server) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        """Patched version of Server.get_prompt that adds Sentry instrumentation."""
        return lambda func: _create_instrumented_decorator(
            original_get_prompt, "prompt", self
        )(func)

    Server.get_prompt = patched_get_prompt

    # Patch read_resource decorator
    original_read_resource = Server.read_resource

    def patched_read_resource(self):
        # type: (Server) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        """Patched version of Server.read_resource that adds Sentry instrumentation."""
        return lambda func: _create_instrumented_decorator(
            original_read_resource, "resource", self
        )(func)

    Server.read_resource = patched_read_resource

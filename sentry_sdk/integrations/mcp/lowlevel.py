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
from sentry_sdk.integrations.mcp.transport import detect_mcp_transport_from_context
from sentry_sdk.utils import safe_serialize

from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import request_ctx


if TYPE_CHECKING:
    from typing import Any, Callable


def _get_span_config(handler_type, item_name):
    # type: (str, str) -> tuple[str, str, str, str | None]
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
    span, handler_name, span_data_key, mcp_method_name, arguments, request_id=None
):
    # type: (Any, str, str, str, dict[str, Any], str | None) -> None
    """Set input span data for MCP handlers."""
    # Set handler identifier
    span.set_data(span_data_key, handler_name)
    span.set_data(SPANDATA.MCP_METHOD_NAME, mcp_method_name)

    # Detect and set transport if available
    try:
        ctx = request_ctx.get()
        transport = detect_mcp_transport_from_context(ctx)
        if transport is not None:
            span.set_data(SPANDATA.MCP_TRANSPORT, transport)
    except LookupError:
        # No request context available - likely stdio
        span.set_data(SPANDATA.MCP_TRANSPORT, "pipe")

    # Set request_id if provided
    if request_id:
        span.set_data(SPANDATA.MCP_REQUEST_ID, request_id)

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
    # type: (Any, Any, str | None, str) -> None
    """Set output span data for MCP handlers."""
    if result is None:
        return

    # For tools, extract the meaningful content
    if handler_type == "tool":
        extracted = _extract_tool_result_content(result)
        if extracted is not None:
            span.set_data(result_data_key, safe_serialize(extracted))
            # Set content count if result is a dict
            if isinstance(extracted, dict):
                span.set_data(SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT, len(extracted))
    elif handler_type == "prompt":
        # For prompts, count messages and set role/content only for single-message prompts
        try:
            messages = None
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

            # Only set role and content for single-message prompts
            if message_count == 1:
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
            # Silently ignore if we can't extract message info, but still serialize result
            span.set_data(result_data_key, safe_serialize(result))
    # Resources don't capture result content (result_data_key is None)


def patch_lowlevel_server():
    # type: () -> None
    """
    Patches the mcp.server.lowlevel.Server class to instrument handler execution.
    """

    # Patch call_tool decorator
    original_call_tool = Server.call_tool

    def patched_call_tool(self, **kwargs):
        # type: (Server, **Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def async_wrapper(tool_name, arguments):
                    # type: (str, Any) -> Any
                    span_data_key, span_name, mcp_method_name, result_data_key = (
                        _get_span_config("tool", tool_name)
                    )
                    with get_start_span_function()(
                        op=OP.MCP_SERVER,
                        name=span_name,
                        origin=MCPIntegration.origin,
                    ) as span:
                        request_id = None
                        try:
                            ctx = request_ctx.get()
                            request_id = ctx.request_id
                        except LookupError:
                            pass
                        _set_span_input_data(
                            span,
                            tool_name,
                            span_data_key,
                            mcp_method_name,
                            arguments,
                            request_id,
                        )
                        try:
                            result = await func(tool_name, arguments)
                            _set_span_output_data(span, result, result_data_key, "tool")
                            return result
                        except Exception as e:
                            span.set_data(SPANDATA.MCP_TOOL_RESULT_IS_ERROR, True)
                            sentry_sdk.capture_exception(e)
                            raise

                return original_call_tool(self, **kwargs)(async_wrapper)
            else:

                @wraps(func)
                def sync_wrapper(tool_name, arguments):
                    # type: (str, Any) -> Any
                    span_data_key, span_name, mcp_method_name, result_data_key = (
                        _get_span_config("tool", tool_name)
                    )
                    with get_start_span_function()(
                        op=OP.MCP_SERVER,
                        name=span_name,
                        origin=MCPIntegration.origin,
                    ) as span:
                        request_id = None
                        try:
                            ctx = request_ctx.get()
                            request_id = ctx.request_id
                        except LookupError:
                            pass
                        _set_span_input_data(
                            span,
                            tool_name,
                            span_data_key,
                            mcp_method_name,
                            arguments,
                            request_id,
                        )
                        try:
                            result = func(tool_name, arguments)
                            _set_span_output_data(span, result, result_data_key, "tool")
                            return result
                        except Exception as e:
                            span.set_data(SPANDATA.MCP_TOOL_RESULT_IS_ERROR, True)
                            sentry_sdk.capture_exception(e)
                            raise

                return original_call_tool(self, **kwargs)(sync_wrapper)

        return decorator

    Server.call_tool = patched_call_tool  # type: ignore

    # Patch get_prompt decorator
    original_get_prompt = Server.get_prompt

    def patched_get_prompt(self):
        # type: (Server) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def async_wrapper(name, arguments):
                    # type: (str, Any) -> Any
                    span_data_key, span_name, mcp_method_name, result_data_key = (
                        _get_span_config("prompt", name)
                    )
                    with get_start_span_function()(
                        op=OP.MCP_SERVER,
                        name=span_name,
                        origin=MCPIntegration.origin,
                    ) as span:
                        request_id = None
                        try:
                            ctx = request_ctx.get()
                            request_id = ctx.request_id
                        except LookupError:
                            pass
                        # Include name in arguments dict for span data
                        args_with_name = {"name": name}
                        if arguments:
                            args_with_name.update(arguments)
                        _set_span_input_data(
                            span,
                            name,
                            span_data_key,
                            mcp_method_name,
                            args_with_name,
                            request_id,
                        )
                        try:
                            result = await func(name, arguments)
                            _set_span_output_data(
                                span, result, result_data_key, "prompt"
                            )
                            return result
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            raise

                return original_get_prompt(self)(async_wrapper)
            else:

                @wraps(func)
                def sync_wrapper(name, arguments):
                    # type: (str, Any) -> Any
                    span_data_key, span_name, mcp_method_name, result_data_key = (
                        _get_span_config("prompt", name)
                    )
                    with get_start_span_function()(
                        op=OP.MCP_SERVER,
                        name=span_name,
                        origin=MCPIntegration.origin,
                    ) as span:
                        request_id = None
                        try:
                            ctx = request_ctx.get()
                            request_id = ctx.request_id
                        except LookupError:
                            pass
                        # Include name in arguments dict for span data
                        args_with_name = {"name": name}
                        if arguments:
                            args_with_name.update(arguments)
                        _set_span_input_data(
                            span,
                            name,
                            span_data_key,
                            mcp_method_name,
                            args_with_name,
                            request_id,
                        )
                        try:
                            result = func(name, arguments)
                            _set_span_output_data(
                                span, result, result_data_key, "prompt"
                            )
                            return result
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            raise

                return original_get_prompt(self)(sync_wrapper)

        return decorator

    Server.get_prompt = patched_get_prompt  # type: ignore

    # Patch read_resource decorator
    original_read_resource = Server.read_resource

    def patched_read_resource(self):
        # type: (Server) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def async_wrapper(uri):
                    # type: (Any) -> Any
                    uri_str = str(uri) if uri else "unknown"
                    # Extract protocol/scheme from URI
                    protocol = None
                    if hasattr(uri, "scheme"):
                        protocol = uri.scheme
                    elif uri_str and "://" in uri_str:
                        protocol = uri_str.split("://")[0]

                    span_data_key, span_name, mcp_method_name, result_data_key = (
                        _get_span_config("resource", uri_str)
                    )
                    with get_start_span_function()(
                        op=OP.MCP_SERVER,
                        name=span_name,
                        origin=MCPIntegration.origin,
                    ) as span:
                        request_id = None
                        try:
                            ctx = request_ctx.get()
                            request_id = ctx.request_id
                        except LookupError:
                            pass
                        _set_span_input_data(
                            span,
                            uri_str,
                            span_data_key,
                            mcp_method_name,
                            {},
                            request_id,
                        )
                        # Set protocol if found
                        if protocol:
                            span.set_data(SPANDATA.MCP_RESOURCE_PROTOCOL, protocol)
                        try:
                            result = await func(uri)
                            _set_span_output_data(
                                span, result, result_data_key, "resource"
                            )
                            return result
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            raise

                return original_read_resource(self)(async_wrapper)
            else:

                @wraps(func)
                def sync_wrapper(uri):
                    # type: (Any) -> Any
                    uri_str = str(uri) if uri else "unknown"
                    # Extract protocol/scheme from URI
                    protocol = None
                    if hasattr(uri, "scheme"):
                        protocol = uri.scheme
                    elif uri_str and "://" in uri_str:
                        protocol = uri_str.split("://")[0]

                    span_data_key, span_name, mcp_method_name, result_data_key = (
                        _get_span_config("resource", uri_str)
                    )
                    with get_start_span_function()(
                        op=OP.MCP_SERVER,
                        name=span_name,
                        origin=MCPIntegration.origin,
                    ) as span:
                        request_id = None
                        try:
                            ctx = request_ctx.get()
                            request_id = ctx.request_id
                        except LookupError:
                            pass
                        _set_span_input_data(
                            span,
                            uri_str,
                            span_data_key,
                            mcp_method_name,
                            {},
                            request_id,
                        )
                        # Set protocol if found
                        if protocol:
                            span.set_data(SPANDATA.MCP_RESOURCE_PROTOCOL, protocol)
                        try:
                            result = func(uri)
                            _set_span_output_data(
                                span, result, result_data_key, "resource"
                            )
                            return result
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            raise

                return original_read_resource(self)(sync_wrapper)

        return decorator

    Server.read_resource = patched_read_resource  # type: ignore

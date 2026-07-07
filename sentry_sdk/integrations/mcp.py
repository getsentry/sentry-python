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
from sentry_sdk.ai.utils import _set_span_data_attribute, get_start_span_function
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations._wsgi_common import nullcontext
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import package_version, safe_serialize

MCP_PACKAGE_VERSION = package_version("mcp")

try:
    from mcp.server.lowlevel import Server
    from mcp.server.streamable_http import (
        StreamableHTTPServerTransport,
    )

    if MCP_PACKAGE_VERSION and MCP_PACKAGE_VERSION < (2, 0, 0):
        from mcp.server.lowlevel.server import (  # type: ignore[attr-defined]
            request_ctx,
        )
except ImportError:
    raise DidNotEnable("MCP SDK not installed")

try:
    from fastmcp import FastMCP  # type: ignore[import-not-found]
except ImportError:
    FastMCP = None

if MCP_PACKAGE_VERSION and MCP_PACKAGE_VERSION >= (2, 0, 0):
    try:
        from mcp.server.context import (
            ServerRequestContext,
        )
    except ImportError:
        ServerRequestContext = None  # type: ignore[assignment,misc]
else:
    ServerRequestContext = None  # type: ignore[assignment,misc]


if TYPE_CHECKING:
    from typing import Any, Callable, ContextManager, Optional, Tuple, Union

    from starlette.types import Receive, Scope, Send

    from sentry_sdk.traces import StreamedSpan
    from sentry_sdk.tracing import Span


class MCPIntegration(Integration):
    identifier = "mcp"
    origin = "auto.ai.mcp"

    def __init__(self, include_prompts: bool = True) -> None:
        """
        Initialize the MCP integration.

        Args:
            include_prompts: Whether to include prompts (tool results and prompt content)
                             in span data. Requires send_default_pii=True. Default is True.
        """
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once() -> None:
        """
        Patches MCP server classes to instrument handler execution.
        """
        _patch_lowlevel_server()
        _patch_handle_request()

        if FastMCP is not None:
            _patch_fastmcp()


def _get_active_http_scopes(
    ctx: "Optional[Any]" = None,
) -> "Optional[Tuple[Optional[sentry_sdk.Scope], Optional[sentry_sdk.Scope]]]":
    if MCP_PACKAGE_VERSION and MCP_PACKAGE_VERSION < (2, 0, 0):
        if ctx is None:
            try:
                ctx = request_ctx.get()
            except LookupError:
                return None

    if (
        ctx is None
        or not hasattr(ctx, "request")
        or ctx.request is None
        or "state" not in ctx.request.scope
    ):
        return None

    return (
        ctx.request.scope["state"].get("sentry_sdk.isolation_scope"),
        ctx.request.scope["state"].get("sentry_sdk.current_scope"),
    )


def _get_request_context_data(
    ctx: "Optional[Any]" = None,
) -> "tuple[Optional[str], Optional[str], str]":
    """
    Extract request ID, session ID, and MCP transport type from the request context.

    Returns:
        Tuple of (request_id, session_id, mcp_transport).
        - request_id: May be None if not available
        - session_id: May be None if not available
        - mcp_transport: "http", "sse", "stdio"
    """
    request_id: "Optional[str]" = None
    session_id: "Optional[str]" = None
    mcp_transport: str = "stdio"
    if MCP_PACKAGE_VERSION and MCP_PACKAGE_VERSION < (2, 0, 0):
        if ctx is None:
            try:
                ctx = request_ctx.get()
            except LookupError:
                return request_id, session_id, mcp_transport

    if ctx is not None:
        request_id = ctx.request_id
        if hasattr(ctx, "request") and ctx.request is not None:
            request = ctx.request
            # Detect transport type by checking request characteristics
            if hasattr(request, "query_params") and request.query_params.get(
                "session_id"
            ):
                # SSE transport uses query parameter
                mcp_transport = "sse"
                session_id = request.query_params.get("session_id")
            elif hasattr(request, "headers") and request.headers.get("mcp-session-id"):
                # StreamableHTTP transport uses header
                mcp_transport = "http"
                session_id = request.headers.get("mcp-session-id")

    return request_id, session_id, mcp_transport


def _get_span_config(
    handler_type: str, item_name: str
) -> "tuple[str, str, str, Optional[str]]":
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
    span: "Union[StreamedSpan, Span]",
    handler_name: str,
    span_data_key: str,
    mcp_method_name: str,
    arguments: "dict[str, Any]",
    request_id: "Optional[str]",
    session_id: "Optional[str]",
    mcp_transport: str,
) -> None:
    """Set input span data for MCP handlers."""

    # Set handler identifier
    _set_span_data_attribute(span, span_data_key, handler_name)
    _set_span_data_attribute(span, SPANDATA.MCP_METHOD_NAME, mcp_method_name)

    # Set transport/MCP transport type
    _set_span_data_attribute(
        span,
        SPANDATA.NETWORK_TRANSPORT,
        "pipe" if mcp_transport == "stdio" else "tcp",
    )
    _set_span_data_attribute(span, SPANDATA.MCP_TRANSPORT, mcp_transport)

    # Set request_id if provided
    if request_id:
        _set_span_data_attribute(span, SPANDATA.MCP_REQUEST_ID, request_id)

    # Set session_id if provided
    if session_id:
        _set_span_data_attribute(span, SPANDATA.MCP_SESSION_ID, session_id)

    # Set request arguments (excluding common request context objects)
    for k, v in arguments.items():
        _set_span_data_attribute(span, f"mcp.request.argument.{k}", safe_serialize(v))


def _extract_tool_result_content(result: "Any") -> "Any":
    """
    Extract meaningful content from MCP tool result.

    Tool handlers can return:
    - CallToolResult (mcp v2+): Has .content list and optional .structured_content
    - tuple (UnstructuredContent, StructuredContent): Return the structured content (dict)
    - dict (StructuredContent): Return as-is
    - list/Iterable (UnstructuredContent): Extract text from content blocks
    """
    if result is None:
        return None

    # Handle v2 CallToolResult-like objects (has .content list attribute)
    if hasattr(result, "content") and isinstance(
        getattr(result, "content", None), list
    ):
        # This is only present when a tool declares an output_schema
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured
        return _extract_text_from_content_blocks(result.content)

    # Handle CombinationContent: tuple of (UnstructuredContent, StructuredContent)
    if isinstance(result, tuple) and len(result) == 2:
        # Return the structured content (2nd element)
        return result[1]

    # Handle StructuredContent: dict
    if isinstance(result, dict):
        return result

    # Handle UnstructuredContent: iterable of ContentBlock objects
    if hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict)):
        return _extract_text_from_content_blocks(result)

    return result


def _extract_text_from_content_blocks(content_blocks: "Any") -> "Any":
    texts = []
    try:
        for item in content_blocks:
            if hasattr(item, "text"):
                texts.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
    except Exception:
        return content_blocks
    return " ".join(texts) if texts else content_blocks


def _set_span_output_data(
    span: "Union[StreamedSpan, Span]",
    result: "Any",
    result_data_key: "Optional[str]",
    handler_type: str,
) -> None:
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
        if (
            extracted is not None
            and should_include_data
            and result_data_key is not None
        ):
            _set_span_data_attribute(span, result_data_key, safe_serialize(extracted))
            # Set content count if result is a dict
            if isinstance(extracted, dict):
                _set_span_data_attribute(
                    span, SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT, len(extracted)
                )
    elif handler_type == "prompt":
        # For prompts, count messages and set role/content only for single-message prompts
        try:
            messages: "Optional[list[str]]" = None
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
                _set_span_data_attribute(
                    span, SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT, message_count
                )

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
                    _set_span_data_attribute(
                        span, SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE, role
                    )

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

                if content_text and result_data_key is not None:
                    _set_span_data_attribute(span, result_data_key, content_text)
        except Exception:
            # Silently ignore if we can't extract message info
            pass
    # Resources don't capture result content (result_data_key is None)


# Handler data preparation and wrapping


def _is_v2_context(original_args: "tuple[Any, ...]") -> bool:
    """Check if original_args contains a v2 ServerRequestContext as the first element."""
    return (
        ServerRequestContext is not None
        and bool(original_args)
        and isinstance(original_args[0], ServerRequestContext)
    )


def _extract_handler_data_from_params(
    handler_type: str,
    params: "Any",
) -> "tuple[str, dict[str, Any]]":
    """
    Extract handler name and arguments from a v2 typed params object.

    In MCP SDK v2, handlers receive (ctx, params) where params is a typed
    Pydantic model (CallToolRequestParams, GetPromptRequestParams, etc.).
    """
    if handler_type == "tool":
        handler_name = getattr(params, "name", "unknown")
        arguments = getattr(params, "arguments", None) or {}
    elif handler_type == "prompt":
        handler_name = getattr(params, "name", "unknown")
        arguments = getattr(params, "arguments", None) or {}
        arguments = {"name": handler_name, **arguments}
    else:  # resource
        handler_name = str(getattr(params, "uri", "unknown"))
        arguments = {}

    return handler_name, arguments


def _extract_handler_data_from_args(
    handler_type: str,
    original_args: "tuple[Any, ...]",
    original_kwargs: "Optional[dict[str, Any]]" = None,
) -> "tuple[str, dict[str, Any]]":
    """
    Extract handler name and arguments from v1 positional args.

    In MCP SDK v1, handlers receive positional args:
    - Tool: (tool_name, arguments)
    - Prompt: (name, arguments)
    - Resource: (uri,)
    """
    original_kwargs = original_kwargs or {}

    if handler_type == "tool":
        if original_args:
            handler_name = original_args[0]
        elif original_kwargs.get("name"):
            handler_name = original_kwargs["name"]

        arguments = {}
        if len(original_args) > 1:
            arguments = original_args[1]
        elif original_kwargs.get("arguments"):
            arguments = original_kwargs["arguments"]

    elif handler_type == "prompt":
        if original_args:
            handler_name = original_args[0]
        elif original_kwargs.get("name"):
            handler_name = original_kwargs["name"]

        arguments = {}
        if len(original_args) > 1:
            arguments = original_args[1]
        elif original_kwargs.get("arguments"):
            arguments = original_kwargs["arguments"]

        arguments = {"name": handler_name, **(arguments or {})}

    else:  # resource
        handler_name = "unknown"
        if original_args:
            handler_name = str(original_args[0])
        elif original_kwargs.get("uri"):
            handler_name = str(original_kwargs["uri"])

        arguments = {}

    return handler_name, arguments


def _prepare_handler_data(
    handler_type: str,
    original_args: "tuple[Any, ...]",
    original_kwargs: "Optional[dict[str, Any]]" = None,
    params: "Optional[Any]" = None,
) -> "tuple[str, dict[str, Any], str, str, str, Optional[str]]":
    """
    Prepare common handler data for both v1 and v2 MCP SDK.

    Args:
        handler_type: "tool", "prompt", or "resource"
        original_args: Original positional args (v1 path)
        original_kwargs: Original keyword args (v1 path)
        params: Typed params object from v2 ServerRequestContext path

    Returns:
        Tuple of (handler_name, arguments, span_data_key, span_name, mcp_method_name, result_data_key)
    """
    if params is not None:
        handler_name, arguments = _extract_handler_data_from_params(
            handler_type, params
        )
    elif _is_v2_context(original_args):
        handler_name = "unknown"
        arguments = {}
    else:
        handler_name, arguments = _extract_handler_data_from_args(
            handler_type, original_args, original_kwargs
        )

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


async def _handler_wrapper(
    handler_type: str,
    func: "Callable[..., Any]",
    original_args: "tuple[Any, ...]",
    original_kwargs: "Optional[dict[str, Any]]" = None,
    self: "Optional[Any]" = None,
    force_await: bool = True,
) -> "Any":
    """
    Wrapper for MCP handlers.

    Args:
        handler_type: "tool", "prompt", or "resource"
        func: The handler function to wrap
        original_args: Original arguments passed to the handler
        original_kwargs: Original keyword arguments passed to the handler
        self: Optional instance for bound methods
    """
    if original_kwargs is None:
        original_kwargs = {}

    # Detect v1 vs v2: MCP SDK v2 passes (ServerRequestContext, params) to handlers
    ctx: "Optional[Any]" = None
    params: "Optional[Any]" = None
    if (
        ServerRequestContext is not None
        and original_args
        and isinstance(original_args[0], ServerRequestContext)
    ):
        ctx = original_args[0]
        params = original_args[1] if len(original_args) > 1 else None

    (
        handler_name,
        arguments,
        span_data_key,
        span_name,
        mcp_method_name,
        result_data_key,
    ) = _prepare_handler_data(
        handler_type, original_args, original_kwargs, params=params
    )

    scopes = _get_active_http_scopes(ctx=ctx)

    isolation_scope_context: "ContextManager[Any]"
    current_scope_context: "ContextManager[Any]"

    if scopes is None:
        isolation_scope_context = nullcontext()
        current_scope_context = nullcontext()
    else:
        isolation_scope, current_scope = scopes

        isolation_scope_context = (
            nullcontext()
            if isolation_scope is None
            else sentry_sdk.scope.use_isolation_scope(isolation_scope)
        )
        current_scope_context = (
            nullcontext()
            if current_scope is None
            else sentry_sdk.scope.use_scope(current_scope)
        )

    # Get request ID, session ID, and transport from context
    request_id, session_id, mcp_transport = _get_request_context_data(ctx=ctx)

    span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)

    # Start span and execute
    with isolation_scope_context:
        with current_scope_context:
            span_mgr: "Union[Span, StreamedSpan]"
            if span_streaming:
                span_mgr = sentry_sdk.traces.start_span(
                    name=span_name,
                    attributes={
                        "sentry.op": OP.MCP_SERVER,
                        "sentry.origin": MCPIntegration.origin,
                    },
                )
            else:
                span_mgr = get_start_span_function()(
                    op=OP.MCP_SERVER,
                    name=span_name,
                    origin=MCPIntegration.origin,
                )

            with span_mgr as span:
                # Set input span data
                _set_span_input_data(
                    span,
                    handler_name,
                    span_data_key,
                    mcp_method_name,
                    arguments,
                    request_id,
                    session_id,
                    mcp_transport,
                )

                # For resources, extract and set protocol
                if handler_type == "resource":
                    uri = None
                    if params is not None:
                        uri = getattr(params, "uri", None)

                    # v1 scenario
                    if ServerRequestContext is None:
                        if original_args:
                            uri = original_args[0]
                        else:
                            uri = original_kwargs.get("uri")

                    protocol = None
                    if uri is not None and hasattr(uri, "scheme"):
                        protocol = uri.scheme
                    elif handler_name and "://" in handler_name:
                        protocol = handler_name.split("://")[0]
                    if protocol:
                        _set_span_data_attribute(
                            span, SPANDATA.MCP_RESOURCE_PROTOCOL, protocol
                        )

                try:
                    # Execute the async handler
                    if self is not None:
                        original_args = (self, *original_args)

                    result = func(*original_args, **original_kwargs)
                    if force_await or inspect.isawaitable(result):
                        result = await result

                except Exception as e:
                    # Set error flag for tools
                    if handler_type == "tool":
                        _set_span_data_attribute(
                            span, SPANDATA.MCP_TOOL_RESULT_IS_ERROR, True
                        )
                    sentry_sdk.capture_exception(e)
                    raise

                _set_span_output_data(span, result, result_data_key, handler_type)

    return result


def _create_instrumented_decorator(
    original_decorator: "Callable[..., Any]",
    handler_type: str,
    *decorator_args: "Any",
    **decorator_kwargs: "Any",
) -> "Callable[..., Any]":
    """
    Create an instrumented version of an MCP decorator.

    This function intercepts MCP decorators (like @server.call_tool()) and injects
    Sentry instrumentation into the handler registration flow. The returned decorator
    will:
    1. Receive the user's handler function
    2. Pass the instrumented version to the original MCP decorator

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

    def instrumented_decorator(func: "Callable[..., Any]") -> "Callable[..., Any]":
        @wraps(func)
        async def wrapper(*args: "Any") -> "Any":
            return await _handler_wrapper(handler_type, func, args, force_await=False)

        # Then register it with the original MCP decorator
        return original_decorator(*decorator_args, **decorator_kwargs)(wrapper)

    return instrumented_decorator


_METHOD_TO_HANDLER_TYPE = {
    "tools/call": "tool",
    "prompts/get": "prompt",
    "resources/read": "resource",
}

# In MCP SDK v2, tool/prompt/resource handlers are most commonly registered via
# the Server(...) constructor kwargs rather than add_request_handler. The in-tree
# high-level MCPServer also wires its handlers through these kwargs.
_KWARG_TO_HANDLER_TYPE = {
    "on_call_tool": "tool",
    "on_get_prompt": "prompt",
    "on_read_resource": "resource",
}


def _patch_lowlevel_server() -> None:
    """
    Patches the mcp.server.lowlevel.Server class to instrument handler execution.
    """
    if MCP_PACKAGE_VERSION and MCP_PACKAGE_VERSION >= (2, 0, 0):
        _patch_lowlevel_server_v2()
    else:
        _patch_lowlevel_server_v1()


def _patch_lowlevel_server_v1() -> None:
    """Patches v1 Server decorator methods (call_tool, get_prompt, read_resource)."""
    # Patch call_tool decorator
    original_call_tool = Server.call_tool  # type: ignore[attr-defined]

    def patched_call_tool(
        self: "Server", **kwargs: "Any"
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Patched version of Server.call_tool that adds Sentry instrumentation."""
        return lambda func: _create_instrumented_decorator(
            original_call_tool, "tool", self, **kwargs
        )(func)

    Server.call_tool = patched_call_tool  # type: ignore[attr-defined]

    # Patch get_prompt decorator
    original_get_prompt = Server.get_prompt  # type: ignore[attr-defined]

    def patched_get_prompt(
        self: "Server",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Patched version of Server.get_prompt that adds Sentry instrumentation."""
        return lambda func: _create_instrumented_decorator(
            original_get_prompt, "prompt", self
        )(func)

    Server.get_prompt = patched_get_prompt  # type: ignore[attr-defined]

    # Patch read_resource decorator
    original_read_resource = Server.read_resource  # type: ignore[attr-defined]

    def patched_read_resource(
        self: "Server",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Patched version of Server.read_resource that adds Sentry instrumentation."""
        return lambda func: _create_instrumented_decorator(
            original_read_resource, "resource", self
        )(func)

    Server.read_resource = patched_read_resource  # type: ignore[attr-defined]


def _wrap_v2_handler(
    handler_type: str, handler: "Callable[..., Any]"
) -> "Callable[..., Any]":
    """Wrap a v2 (ctx, params) handler with Sentry instrumentation.

    Idempotent: an already-wrapped handler is returned unchanged so handlers
    registered through more than one path (e.g. MCPServer building a Server)
    are not double-wrapped.
    """
    if getattr(handler, "__sentry_mcp_wrapped__", False):
        return handler

    @wraps(handler)
    async def wrapper(*args: "Any", **kwargs: "Any") -> "Any":
        return await _handler_wrapper(
            handler_type, handler, args, kwargs, force_await=False
        )

    wrapper.__sentry_mcp_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper


def _patch_lowlevel_server_v2() -> None:
    """Patches the v2 Server to wrap tool/prompt/resource handlers.

    Handlers can be registered either via the Server(...) constructor kwargs
    (on_call_tool/on_get_prompt/on_read_resource) — the path the in-tree
    MCPServer and most lowlevel examples use — or via add_request_handler.
    Both are patched.
    """
    original_init = Server.__init__

    @wraps(original_init)
    def patched_init(self: "Server", *args: "Any", **kwargs: "Any") -> None:
        for kwarg, handler_type in _KWARG_TO_HANDLER_TYPE.items():
            handler = kwargs.get(kwarg)
            if handler is not None:
                kwargs[kwarg] = _wrap_v2_handler(handler_type, handler)
        original_init(self, *args, **kwargs)

    Server.__init__ = patched_init  # type: ignore[method-assign]

    original_add_request_handler = Server.add_request_handler

    def patched_add_request_handler(
        self: "Server",
        method: str,
        params_type: "Any",
        handler: "Callable[..., Any]",
        *args: "Any",
        **kwargs: "Any",
    ) -> None:
        handler_type = _METHOD_TO_HANDLER_TYPE.get(method)
        if handler_type is not None:
            handler = _wrap_v2_handler(handler_type, handler)

        original_add_request_handler(
            self, method, params_type, handler, *args, **kwargs
        )

    Server.add_request_handler = patched_add_request_handler  # type: ignore[method-assign]


def _patch_handle_request() -> None:
    original_handle_request = StreamableHTTPServerTransport.handle_request

    @wraps(original_handle_request)
    async def patched_handle_request(
        self: "StreamableHTTPServerTransport",
        scope: "Scope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        scope.setdefault("state", {})["sentry_sdk.isolation_scope"] = (
            sentry_sdk.get_isolation_scope()
        )
        scope["state"]["sentry_sdk.current_scope"] = sentry_sdk.get_current_scope()
        await original_handle_request(self, scope, receive, send)

    StreamableHTTPServerTransport.handle_request = patched_handle_request  # type: ignore[method-assign]


def _patch_fastmcp() -> None:
    """
    Patches the standalone fastmcp package's FastMCP class.

    The standalone fastmcp package (v2.14.0+) registers its own handlers for
    prompts and resources directly, bypassing the Server decorators we patch.
    This function patches the _get_prompt_mcp and _read_resource_mcp methods
    to add instrumentation for those handlers.
    """
    if FastMCP is not None and hasattr(FastMCP, "_get_prompt_mcp"):
        original_get_prompt_mcp = FastMCP._get_prompt_mcp

        @wraps(original_get_prompt_mcp)
        async def patched_get_prompt_mcp(
            self: "Any", *args: "Any", **kwargs: "Any"
        ) -> "Any":
            return await _handler_wrapper(
                "prompt",
                original_get_prompt_mcp,
                args,
                kwargs,
                self,
            )

        FastMCP._get_prompt_mcp = patched_get_prompt_mcp

    if FastMCP is not None and hasattr(FastMCP, "_read_resource_mcp"):
        original_read_resource_mcp = FastMCP._read_resource_mcp

        @wraps(original_read_resource_mcp)
        async def patched_read_resource_mcp(
            self: "Any", *args: "Any", **kwargs: "Any"
        ) -> "Any":
            return await _handler_wrapper(
                "resource",
                original_read_resource_mcp,
                args,
                kwargs,
                self,
            )

        FastMCP._read_resource_mcp = patched_read_resource_mcp

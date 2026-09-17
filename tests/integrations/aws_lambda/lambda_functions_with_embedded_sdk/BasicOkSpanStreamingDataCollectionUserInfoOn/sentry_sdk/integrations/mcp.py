"""
Sentry integration for MCP (Model Context Protocol) servers.

This integration instruments MCP servers to create spans for tool, prompt,
and resource handler execution, and captures errors that occur during execution.

Supports the low-level `mcp.server.lowlevel.Server` API.
"""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    has_data_collection_enabled,
    package_version,
    safe_serialize,
)

try:
    from mcp.server.lowlevel import Server
    from mcp.server.streamable_http import (
        StreamableHTTPServerTransport,
    )

    MCP_PACKAGE_VERSION = package_version("mcp")

except ImportError:
    raise DidNotEnable("MCP SDK not installed or incompatible")


if TYPE_CHECKING:
    from typing import Any, Optional

    from mcp.server.context import (
        CallNext,
        HandlerResult,
        ServerRequestContext,
    )
    from starlette.types import Receive, Scope, Send

    from sentry_sdk.traces import Span


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
        _check_minimum_version(MCPIntegration, MCP_PACKAGE_VERSION)

        _patch_lowlevel_server()
        _patch_handle_request()


def _capture_exception(exc: "Any") -> None:
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "mcp", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


@contextmanager
def _active_http_scopes(
    ctx: "Any",
) -> "Iterator[None]":
    """
    Use isolation and current scopes that were stored before the in-memory MCP request queue.
    This ensures that MCP spans are nested under the HTTP server span when using the Streamable HTTP transport.
    """
    if (
        ctx is None
        or not hasattr(ctx, "request")
        or ctx.request is None
        or "state" not in ctx.request.scope
    ):
        yield
        return

    isolation_scope = ctx.request.scope["state"].get("sentry_sdk.isolation_scope")
    current_scope = ctx.request.scope["state"].get("sentry_sdk.current_scope")

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

    with isolation_scope_context, current_scope_context:
        yield


def _get_request_context_data(
    ctx: "Any",
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
            elif hasattr(request, "headers"):
                # StreamableHTTP transport uses header
                mcp_transport = "http"
                session_id = request.headers.get("mcp-session-id")

    return request_id, session_id, mcp_transport


def _set_span_input_data(
    span: "Span",
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
    span.set_attribute(span_data_key, handler_name)
    span.set_attribute(SPANDATA.MCP_METHOD_NAME, mcp_method_name)

    # Set transport/MCP transport type
    span.set_attribute(
        SPANDATA.NETWORK_TRANSPORT,
        "pipe" if mcp_transport == "stdio" else "tcp",
    )
    span.set_attribute(SPANDATA.MCP_TRANSPORT, mcp_transport)

    # Set request_id if provided
    if request_id:
        span.set_attribute(SPANDATA.MCP_REQUEST_ID, request_id)

    # Set session_id if provided
    if session_id:
        span.set_attribute(SPANDATA.MCP_SESSION_ID, session_id)

    # Set request arguments (excluding common request context objects)
    for k, v in arguments.items():
        span.set_attribute(f"mcp.request.argument.{k}", safe_serialize(v))


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


async def _instrument_tool_call(
    ctx: "ServerRequestContext[Any, Any]",
    call_next: "CallNext",
) -> "HandlerResult":
    """
    Instrument a tool call as observed by the MCP Server middleware.
    Creates and manages the MCP span and attaches all attributes on the span.
    """
    if ctx.params is None or ctx.params.get("name") is None:
        return await call_next(ctx)

    client = sentry_sdk.get_client()
    handler_name = ctx.params["name"]
    arguments = ctx.params.get("arguments")
    if arguments is None:
        arguments = {}

    if has_data_collection_enabled(client.options):
        if not client.options["data_collection"]["gen_ai"]["inputs"]:
            # Arguments can contain sensitive data and shouldn't be added to the span
            # if the user has opted out via this config.
            arguments = {}

    # Get request ID, session ID, and transport from context
    request_id, session_id, mcp_transport = _get_request_context_data(ctx=ctx)

    # Start span and execute
    with _active_http_scopes(ctx=ctx), sentry_sdk.traces.start_span(
        name=f"tools/call {handler_name}",
        attributes={
            "sentry.op": OP.MCP_SERVER,
            "sentry.origin": MCPIntegration.origin,
        },
    ) as span:
        # Set input span data
        _set_span_input_data(
            span,
            handler_name,
            SPANDATA.MCP_TOOL_NAME,
            "tools/call",
            arguments,
            request_id,
            session_id,
            mcp_transport,
        )

        try:
            result = await call_next(ctx)
        except Exception as e:
            with capture_internal_exceptions():
                _capture_exception(e)
            raise

        if not isinstance(result, dict):
            return result

        # Get integration to check PII settings
        integration = client.get_integration(MCPIntegration)
        if integration is None:
            return result

        # Check if we should include sensitive data
        should_include_result_data = False
        if has_data_collection_enabled(client.options):
            if client.options["data_collection"]["gen_ai"]["outputs"]:
                should_include_result_data = True
        elif should_send_default_pii() and integration.include_prompts:
            should_include_result_data = True

        result_content = result
        if "structuredContent" in result:
            result_content = result["structuredContent"]
        elif isinstance(result.get("content"), list):
            result_content = _extract_text_from_content_blocks(result["content"])

        if result_content is not None and should_include_result_data:
            span.set_attribute(
                SPANDATA.MCP_TOOL_RESULT_CONTENT,
                safe_serialize(result_content),
            )
            # Set content count if result is a dict
            if isinstance(result_content, dict):
                span.set_attribute(
                    SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT,
                    len(result_content),
                )

    return result


async def _instrument_prompt_get(
    ctx: "ServerRequestContext[Any, Any]",
    call_next: "CallNext",
) -> "HandlerResult":
    """
    Instrument a prompt retrieval as observed by the MCP Server middleware.
    Creates and manages the MCP span and attaches all attributes on the span.
    """
    if ctx.params is None or ctx.params.get("name") is None:
        return await call_next(ctx)

    client = sentry_sdk.get_client()
    handler_name = ctx.params["name"]

    arguments = ctx.params.get("arguments")

    if arguments is None:
        arguments = {}

    if has_data_collection_enabled(client.options):
        if not client.options["data_collection"]["gen_ai"]["inputs"]:
            # Arguments can contain sensitive data and shouldn't be added to the span
            # if the user has opted out via this config.
            arguments = {}

    # Get request ID, session ID, and transport from context
    request_id, session_id, mcp_transport = _get_request_context_data(ctx=ctx)

    # Start span and execute
    with _active_http_scopes(ctx=ctx), sentry_sdk.traces.start_span(
        name=f"prompts/get {handler_name}",
        attributes={
            "sentry.op": OP.MCP_SERVER,
            "sentry.origin": MCPIntegration.origin,
        },
    ) as span:
        # Set input span data
        _set_span_input_data(
            span,
            handler_name,
            SPANDATA.MCP_PROMPT_NAME,
            "prompts/get",
            arguments,
            request_id,
            session_id,
            mcp_transport,
        )

        try:
            result = await call_next(ctx)
        except Exception as e:
            with capture_internal_exceptions():
                _capture_exception(e)
            raise

        if not isinstance(result, dict):
            return result

        # Get integration to check PII settings
        integration = client.get_integration(MCPIntegration)
        if integration is None:
            return result

        # Check if we should include sensitive data
        should_include_result_data = False
        if has_data_collection_enabled(client.options):
            if client.options["data_collection"]["gen_ai"]["inputs"]:
                should_include_result_data = True
        elif should_send_default_pii() and integration.include_prompts:
            should_include_result_data = True

        # For prompts, count messages and set role/content only for single-message prompts
        try:
            messages: "Optional[list[dict[str, Any]]]" = None
            message_count = 0

            if result.get("messages"):
                messages = result["messages"]
                message_count = len(messages)

            # Always set message count if we found messages
            if message_count > 0:
                span.set_attribute(
                    SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT, message_count
                )

            # Only set role and content for single-message prompts if PII is allowed
            if message_count == 1 and should_include_result_data and messages:
                first_message = messages[0]
                # Extract role
                role = None
                if "role" in first_message:
                    role = first_message["role"]

                if role:
                    span.set_attribute(SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE, role)

                content_text = None
                if "content" in first_message:
                    msg_content = first_message["content"]
                    if "text" in msg_content:
                        content_text = msg_content["text"]

                if content_text:
                    span.set_attribute(
                        SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT,
                        content_text,
                    )
        except Exception:
            # Silently ignore if we can't extract message info
            pass

    return result


async def _instrument_resource_read(
    ctx: "ServerRequestContext[Any, Any]",
    call_next: "CallNext",
) -> "HandlerResult":
    """
    Instrument getting a resource as observed by the MCP Server middleware.
    Creates and manages the MCP span and attaches all attributes on the span.
    """
    if ctx.params is None or ctx.params.get("uri") is None:
        return await call_next(ctx)

    handler_name = ctx.params["uri"]

    # Get request ID, session ID, and transport from context
    request_id, session_id, mcp_transport = _get_request_context_data(ctx=ctx)

    # Start span and execute
    with _active_http_scopes(ctx=ctx), sentry_sdk.traces.start_span(
        name=f"resources/read {handler_name}",
        attributes={
            "sentry.op": OP.MCP_SERVER,
            "sentry.origin": MCPIntegration.origin,
        },
    ) as span:
        # Set input span data
        _set_span_input_data(
            span,
            handler_name,
            SPANDATA.MCP_RESOURCE_URI,
            "resources/read",
            {},
            request_id,
            session_id,
            mcp_transport,
        )

        protocol = None
        if handler_name and "://" in handler_name:
            protocol = handler_name.split("://")[0]
        if protocol:
            span.set_attribute(SPANDATA.MCP_RESOURCE_PROTOCOL, protocol)

        try:
            result = await call_next(ctx)

        except Exception as e:
            with capture_internal_exceptions():
                _capture_exception(e)
            raise

    return result


async def _sentry_middleware(
    ctx: "ServerRequestContext[Any, Any]", call_next: "CallNext"
) -> "HandlerResult":
    if ctx.method == "tools/call":
        return await _instrument_tool_call(ctx, call_next)

    if ctx.method == "prompts/get":
        return await _instrument_prompt_get(ctx, call_next)

    if ctx.method == "resources/read":
        return await _instrument_resource_read(ctx, call_next)

    return await call_next(ctx)


def _patch_lowlevel_server() -> None:
    """Patches the v2 Server to wrap tool/prompt/resource handlers.

    Handlers can be registered either via the Server(...) constructor kwargs
    (on_call_tool/on_get_prompt/on_read_resource) — the path the in-tree
    MCPServer and most lowlevel examples use — or via add_request_handler.
    Both are patched.
    """
    original_init = Server.__init__

    @wraps(original_init)
    def patched_init(self: "Server", *args: "Any", **kwargs: "Any") -> None:
        original_init(self, *args, **kwargs)
        self.middleware.append(_sentry_middleware)

    Server.__init__ = patched_init  # type: ignore[method-assign]


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

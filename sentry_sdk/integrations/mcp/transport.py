"""
Transport detection and session tracking for MCP servers.

This module provides functionality to:
1. Detect which transport method is being used (stdio/pipe vs HTTP-based transports)
2. Track session IDs for HTTP-based transports

Transport detection is done lazily by inspecting the request context at runtime:
- If there's an HTTP request context (SSE/WebSocket), transport is "tcp"
- If there's no request context (stdio), transport is "pipe"

Session ID tracking is done by patching Server._run_request_handler to store a
reference to the server instance in the request context. The session ID can then
be retrieved from the server's transport when needed during handler execution.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Any


def detect_mcp_transport_from_context(request_ctx):
    # type: (Any) -> Optional[str]
    """
    Detect MCP transport type from the request context.

    Args:
        request_ctx: The MCP request context object (from request_ctx.get())

    Returns:
        "pipe" for stdio transport, "tcp" for HTTP-based transports (SSE/WebSocket),
        or None if transport type cannot be determined.

    Detection logic:
        - If request_ctx has a 'request' attribute with a value, it's HTTP-based → "tcp"
        - If request_ctx exists but has no 'request' or request is None, it's stdio → "pipe"
        - If we can't determine, return None
    """
    try:
        # Check if there's a request object in the context
        # SSE and WebSocket transports set this via ServerMessageMetadata
        if hasattr(request_ctx, "request") and request_ctx.request is not None:
            # This is SSE or WebSocket (HTTP-based)
            return "tcp"
        elif hasattr(request_ctx, "request"):
            # Context exists but no HTTP request - this is stdio
            return "pipe"
    except Exception:
        # If anything goes wrong, return None
        pass

    return None


def get_session_id_from_context(request_ctx):
    # type: (Any) -> Optional[str]
    """
    Extract session ID from the request context.

    The session ID is sent by the client in the MCP-Session-Id header and is
    available in the Starlette Request object stored in ctx.request.

    Args:
        request_ctx: The MCP request context object

    Returns:
        Session ID string if available, None otherwise
    """
    try:
        # The Starlette Request object is stored in ctx.request
        if hasattr(request_ctx, "request") and request_ctx.request is not None:
            request = request_ctx.request

            # Check if it's a Starlette Request with headers
            if hasattr(request, "headers"):
                # The session ID is sent in the mcp-session-id header
                # MCP_SESSION_ID_HEADER = "mcp-session-id"
                return request.headers.get("mcp-session-id")

    except Exception:
        pass

    return None

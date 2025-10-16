"""
Transport detection for MCP servers.

This module provides functionality to detect which transport method is being used
by an MCP server (stdio/pipe vs HTTP-based transports like SSE/WebSocket).

Detection is done lazily by inspecting the request context at runtime:
- If there's an HTTP request context (SSE/WebSocket), transport is "tcp"
- If there's no request context (stdio), transport is "pipe"
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

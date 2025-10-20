"""
Transport detection and session tracking for MCP servers.

This module provides functionality to:
1. Detect which transport method is being used (stdio/pipe vs HTTP-based transports)
2. Track session IDs for HTTP-based transports

Transport detection is done lazily by inspecting the request context at runtime:
- If there's an HTTP request context (SSE/WebSocket), transport is "tcp"
- If there's no request context (stdio), transport is "pipe"

Session ID tracking is done by patching the StreamableHTTPServerTransport to store
the session ID in a context variable that can be accessed during handler execution.
"""

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Any
    from starlette.types import Receive, Scope, Send


# Context variable to store the current MCP session ID
mcp_session_id_ctx = contextvars.ContextVar("mcp_session_id", default=None)  # type: contextvars.ContextVar[Optional[str]]


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


def patch_streamable_http_transport():
    # type: () -> None
    """
    Patches the StreamableHTTPServerTransport to store session IDs in context.

    This allows handler code to access the session ID via the mcp_session_id_ctx
    context variable, regardless of whether it's the first request (where the
    session ID hasn't been sent to the client yet) or a subsequent request.
    """
    try:
        from mcp.server.streamable_http import StreamableHTTPServerTransport
    except ImportError:
        # StreamableHTTP transport not available
        return

    original_handle_request = StreamableHTTPServerTransport.handle_request

    async def patched_handle_request(self, scope, receive, send):
        # type: (Any, Scope, Receive, Send) -> None
        """Wrap handle_request to set session ID in context."""
        # Store session ID in context variable before handling request
        token = None
        if hasattr(self, "mcp_session_id") and self.mcp_session_id:
            token = mcp_session_id_ctx.set(self.mcp_session_id)

        try:
            await original_handle_request(self, scope, receive, send)
        finally:
            # Reset context after request
            if token is not None:
                mcp_session_id_ctx.reset(token)

    StreamableHTTPServerTransport.handle_request = patched_handle_request  # type: ignore

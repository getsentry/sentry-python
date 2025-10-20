"""
Sentry integration for MCP (Model Context Protocol) servers.

This integration instruments MCP servers to create spans for tool, prompt,
and resource handler execution, and captures errors that occur during execution.

Supports both the low-level `mcp.server.lowlevel.Server` and high-level
`mcp.server.fastmcp.FastMCP` APIs.
"""

from sentry_sdk.integrations import Integration, DidNotEnable

try:
    import mcp.server.lowlevel  # noqa: F401
except ImportError:
    raise DidNotEnable("MCP SDK not installed")


class MCPIntegration(Integration):
    identifier = "mcp"
    origin = "auto.ai.mcp"

    @staticmethod
    def setup_once():
        # type: () -> None
        """
        Patches MCP server classes to instrument handler execution.
        """
        from sentry_sdk.integrations.mcp.lowlevel import patch_lowlevel_server
        from sentry_sdk.integrations.mcp.transport import (
            patch_streamable_http_transport,
        )

        # Patch server classes to instrument handlers
        patch_lowlevel_server()

        # Patch HTTP transport to track session IDs
        patch_streamable_http_transport()


__all__ = ["MCPIntegration"]

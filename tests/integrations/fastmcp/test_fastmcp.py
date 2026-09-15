"""
Unit tests for the Sentry MCP integration with FastMCP.

This test suite verifies that Sentry's MCPIntegration properly instruments
both FastMCP implementations:
- mcp.server.fastmcp.FastMCP (FastMCP from the mcp package)
- fastmcp.FastMCP (standalone fastmcp package)

Tests focus on verifying Sentry integration behavior:
- Integration doesn't break FastMCP functionality
- Span creation when tools/prompts/resources are called through MCP protocol
- Span data accuracy (operation, name, origin, etc.)
- Error capture and instrumentation
- PII and include_prompts flag behavior
- Request context data extraction
- Transport detection (stdio, http, sse)

All tests invoke tools/prompts/resources through the MCP Server's low-level
request handlers (via CallToolRequest, GetPromptRequest, ReadResourceRequest)
to properly trigger Sentry instrumentation and span creation. This ensures
accurate testing of the integration's behavior in real MCP Server scenarios.
"""

import asyncio
import json
import logging
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.mcp import MCPIntegration

try:
    from fastmcp.prompts import Message
except ImportError:
    Message = None


from starlette.applications import Starlette
from starlette.routing import Mount

# Try to import both FastMCP implementations
try:
    from mcp.server.fastmcp import FastMCP as MCPFastMCP

    HAS_MCP_FASTMCP = True
except ImportError:
    HAS_MCP_FASTMCP = False
    MCPFastMCP = None

try:
    from fastmcp import FastMCP as StandaloneFastMCP

    HAS_STANDALONE_FASTMCP = True
except ImportError:
    HAS_STANDALONE_FASTMCP = False
    StandaloneFastMCP = None

# Try to import request_ctx for context testing
try:
    from mcp.server.lowlevel.server import request_ctx
except ImportError:
    request_ctx = None

# Try to import MCP types for helper functions
try:
    from mcp.types import CallToolRequest, GetPromptRequest, ReadResourceRequest
except ImportError:
    # If mcp.types not available, tests will be skipped anyway
    CallToolRequest = None
    GetPromptRequest = None
    ReadResourceRequest = None

# Collect available FastMCP implementations for parametrization
fastmcp_implementations = []
fastmcp_ids = []

if HAS_MCP_FASTMCP:
    fastmcp_implementations.append(MCPFastMCP)
    fastmcp_ids.append("mcp.server.fastmcp")

if HAS_STANDALONE_FASTMCP:
    fastmcp_implementations.append(StandaloneFastMCP)
    fastmcp_ids.append("fastmcp")


# Helper functions to call tools through MCP Server protocol
def call_tool_through_mcp(mcp_instance, tool_name, arguments):
    """
    Call a tool through MCP Server's low-level handler.
    This properly triggers Sentry instrumentation.

    Args:
        mcp_instance: The FastMCP instance
        tool_name: Name of the tool to call
        arguments: Dictionary of arguments to pass to the tool

    Returns:
        The tool result normalized to {"result": value} format
    """
    handler = mcp_instance._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(
        method="tools/call", params={"name": tool_name, "arguments": arguments}
    )

    result = asyncio.run(handler(request))

    if hasattr(result, "root"):
        result = result.root
    if hasattr(result, "structuredContent") and result.structuredContent:
        result = result.structuredContent
    elif hasattr(result, "content"):
        if result.content:
            text = result.content[0].text
            try:
                result = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                result = text
        else:
            # Empty content means None return
            result = None

    # Normalize return value to consistent format
    # If already a dict, return as-is (tool functions return dicts directly)
    if isinstance(result, dict):
        return result

    # Handle string "None" or "null" as actual None
    if isinstance(result, str) and result in ("None", "null"):
        result = None

    # Wrap primitive values (int, str, bool, None) in dict format for consistency
    return {"result": result}


async def call_tool_through_mcp_async(mcp_instance, tool_name, arguments):
    """Async version of call_tool_through_mcp."""
    handler = mcp_instance._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(
        method="tools/call", params={"name": tool_name, "arguments": arguments}
    )

    result = await handler(request)

    if hasattr(result, "root"):
        result = result.root
    if hasattr(result, "structuredContent") and result.structuredContent:
        result = result.structuredContent
    elif hasattr(result, "content"):
        if result.content:
            text = result.content[0].text
            try:
                result = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                result = text
        else:
            # Empty content means None return
            result = None

    # Normalize return value to consistent format
    # If already a dict, return as-is (tool functions return dicts directly)
    if isinstance(result, dict):
        return result

    # Handle string "None" or "null" as actual None
    if isinstance(result, str) and result in ("None", "null"):
        result = None

    # Wrap primitive values (int, str, bool, None) in dict format for consistency
    return {"result": result}


def call_prompt_through_mcp(mcp_instance, prompt_name, arguments=None):
    """Call a prompt through MCP Server's low-level handler."""
    handler = mcp_instance._mcp_server.request_handlers[GetPromptRequest]
    request = GetPromptRequest(
        method="prompts/get", params={"name": prompt_name, "arguments": arguments or {}}
    )

    result = asyncio.run(handler(request))
    if hasattr(result, "root"):
        result = result.root
    return result


async def call_prompt_through_mcp_async(mcp_instance, prompt_name, arguments=None):
    """Async version of call_prompt_through_mcp."""
    handler = mcp_instance._mcp_server.request_handlers[GetPromptRequest]
    request = GetPromptRequest(
        method="prompts/get", params={"name": prompt_name, "arguments": arguments or {}}
    )

    result = await handler(request)
    if hasattr(result, "root"):
        result = result.root
    return result


def call_resource_through_mcp(mcp_instance, uri):
    """Call a resource through MCP Server's low-level handler."""
    handler = mcp_instance._mcp_server.request_handlers[ReadResourceRequest]
    request = ReadResourceRequest(method="resources/read", params={"uri": str(uri)})

    result = asyncio.run(handler(request))
    if hasattr(result, "root"):
        result = result.root
    return result


async def call_resource_through_mcp_async(mcp_instance, uri):
    """Async version of call_resource_through_mcp."""
    handler = mcp_instance._mcp_server.request_handlers[ReadResourceRequest]
    request = ReadResourceRequest(method="resources/read", params={"uri": str(uri)})

    result = await handler(request)
    if hasattr(result, "root"):
        result = result.root
    return result


# Skip all tests if neither implementation is available
pytestmark = pytest.mark.skipif(
    not (HAS_MCP_FASTMCP or HAS_STANDALONE_FASTMCP),
    reason="Neither mcp.fastmcp nor standalone fastmcp is installed",
)


@pytest.fixture(autouse=True)
def reset_request_ctx():
    """Reset request context before and after each test"""
    if request_ctx is not None:
        try:
            if request_ctx.get() is not None:
                request_ctx.set(None)
        except LookupError:
            pass

    yield

    if request_ctx is not None:
        try:
            request_ctx.set(None)
        except LookupError:
            pass


# =============================================================================
# Tool Handler Tests - Verifying Sentry Integration
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_fastmcp_tool_sync(
    sentry_init,
    capture_items,
    FastMCP,
    send_default_pii,
    include_prompts,
    stdio,
):
    """Test that FastMCP synchronous tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def add_numbers(a: int, b: int) -> dict:
        """Add two numbers together"""
        return {"result": a + b, "operation": "addition"}

    items = capture_items("span")

    # Call through MCP protocol to trigger instrumentation
    await stdio(
        mcp._mcp_server,
        method="tools/call",
        params={
            "name": "add_numbers",
            "arguments": {"a": 10, "b": 5},
        },
        request_id="req-123",
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 1

    # Verify span structure
    span = spans[0]
    assert span["attributes"]["sentry.op"] == OP.MCP_SERVER
    assert span["attributes"]["sentry.origin"] == "auto.ai.mcp"
    assert span["name"] == "tools/call add_numbers"
    assert span["attributes"][SPANDATA.MCP_TOOL_NAME] == "add_numbers"
    assert span["attributes"][SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert span["attributes"][SPANDATA.MCP_TRANSPORT] == "stdio"
    assert span["attributes"][SPANDATA.MCP_REQUEST_ID] == "req-123"

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT in span["attributes"]
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["attributes"]


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_fastmcp_tool_async(
    sentry_init, capture_items, FastMCP, send_default_pii, include_prompts, json_rpc
):
    """Test that FastMCP async tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )

    mcp = FastMCP("Test Server")

    session_manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server,
        json_response=True,
    )

    app = Starlette(
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    @mcp.tool()
    async def multiply_numbers(x: int, y: int) -> dict:
        """Multiply two numbers together"""
        return {"result": x * y, "operation": "multiplication"}

    items = capture_items("span")

    session_id, result = json_rpc(
        app,
        method="tools/call",
        params={
            "name": "multiply_numbers",
            "arguments": {"x": 7, "y": 6},
        },
        request_id="req-456",
    )

    assert json.loads(result.json()["result"]["content"][0]["text"]) == {
        "result": 42,
        "operation": "multiplication",
    }

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    spans = [
        span
        for span in spans
        if span["attributes"].get("mcp.method.name") == "tools/call"
    ]
    assert len(spans) == 1
    span = spans[0]

    assert span["attributes"]["sentry.op"] == OP.MCP_SERVER
    assert span["attributes"]["sentry.origin"] == "auto.ai.mcp"
    assert span["name"] == "tools/call multiply_numbers"
    assert span["attributes"][SPANDATA.MCP_TOOL_NAME] == "multiply_numbers"
    assert span["attributes"][SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert span["attributes"][SPANDATA.MCP_TRANSPORT] == "http"
    assert span["attributes"][SPANDATA.MCP_REQUEST_ID] == "req-456"
    assert span["attributes"][SPANDATA.MCP_SESSION_ID] == session_id

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT in span["attributes"]
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["attributes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_tool_with_error(
    sentry_init,
    capture_items,
    FastMCP,
    stdio,
):
    """Test that FastMCP tool handler errors are captured properly"""
    # TODO: This test doesn't capture errors via the MCP integration, but rather
    # via logging. Might be worth another look if that's intended.
    sentry_init(
        integrations=[MCPIntegration(), LoggingIntegration(event_level=logging.ERROR)],
        traces_sample_rate=1.0,
    )

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def failing_tool(value: int) -> int:
        """A tool that always fails"""
        raise ValueError("Tool execution failed")

    items = capture_items("event", "span")
    await stdio(
        mcp._mcp_server,
        method="tools/call",
        params={
            "name": "failing_tool",
            "arguments": {"value": 42},
        },
        request_id="req-error",
    )

    sentry_sdk.flush()
    # Check span was created
    spans = [item.payload for item in items if item.type == "span"]
    tool_spans = [s for s in spans if s["attributes"].get("sentry.op") == OP.MCP_SERVER]

    assert len(tool_spans) == 1

    # Check error event was captured
    events = [item.payload for item in items if item.type == "event"]
    error_events = [e for e in events if e.get("level") == "error"]
    assert len(error_events) >= 1
    error_event = error_events[0]
    assert error_event["exception"]["values"][0]["type"] == "ValueError"
    assert error_event["exception"]["values"][0]["value"] == "Tool execution failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_tool_with_complex_return(
    sentry_init,
    capture_items,
    FastMCP,
    stdio,
):
    """Test FastMCP tool with complex nested return value"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def get_user_data(user_id: int) -> dict:
        """Get complex user data"""
        return {
            "id": user_id,
            "name": "Alice",
            "nested": {"preferences": {"theme": "dark", "notifications": True}},
            "tags": ["admin", "verified"],
        }

    items = capture_items("span")
    await stdio(
        mcp._mcp_server,
        method="tools/call",
        params={
            "name": "get_user_data",
            "arguments": {"user_id": 123},
        },
        request_id="req-complex",
    )

    sentry_sdk.flush()
    # Verify span was created with complex data
    spans = [item.payload for item in items]
    tool_spans = [s for s in spans if s["attributes"].get("sentry.op") == OP.MCP_SERVER]
    assert len(tool_spans) == 1
    assert tool_spans[0]["attributes"]["sentry.op"] == OP.MCP_SERVER
    assert tool_spans[0]["attributes"][SPANDATA.MCP_TOOL_NAME] == "get_user_data"
    # Complex return value should be captured since include_prompts=True and send_default_pii=True
    assert SPANDATA.MCP_TOOL_RESULT_CONTENT in tool_spans[0]["attributes"]


# =============================================================================
# Prompt Handler Tests (if supported)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_fastmcp_prompt_sync(
    sentry_init,
    capture_items,
    FastMCP,
    send_default_pii,
    include_prompts,
    stdio,
):
    """Test that FastMCP synchronous prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )

    mcp = FastMCP("Test Server")

    # Try to register a prompt handler (may not be supported in all versions)
    if hasattr(mcp, "prompt"):

        @mcp.prompt()
        def code_help_prompt(language: str):
            """Get help for a programming language"""
            message = Message(
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"Tell me about {language}",
                    },
                }
            )

            return [message]

        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            await stdio(
                mcp._mcp_server,
                method="prompts/get",
                params={
                    "name": "code_help_prompt",
                    "arguments": {"language": "python"},
                },
                request_id="req-prompt",
            )

        sentry_sdk.flush()
        # Verify prompt span was created
        spans = [item.payload for item in items]
        prompt_spans = [
            s for s in spans if s["attributes"].get("sentry.op") == OP.MCP_SERVER
        ]
        assert len(prompt_spans) == 1
        span = prompt_spans[0]
        assert span["attributes"]["sentry.origin"] == "auto.ai.mcp"
        assert span["name"] == "prompts/get code_help_prompt"
        assert span["attributes"][SPANDATA.MCP_PROMPT_NAME] == "code_help_prompt"

        # Check PII-sensitive data
        if send_default_pii and include_prompts:
            assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT in span["attributes"]
        else:
            assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in span["attributes"]


# =============================================================================
# Resource Handler Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_resource_sync(
    sentry_init,
    capture_items,
    FastMCP,
    stdio,
):
    """Test that FastMCP synchronous resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    mcp = FastMCP("Test Server")

    # Try to register a resource handler
    if hasattr(mcp, "resource"):

        @mcp.resource("file:///{path}")
        def read_file(path: str):
            """Read a file resource"""
            return "file contents"

        items = capture_items("span")
        try:
            await stdio(
                mcp._mcp_server,
                method="resources/read",
                params={
                    "uri": "file:///test.txt",
                },
                request_id="req-resource",
            )
        except ValueError as e:
            # Older FastMCP versions may not support this URI pattern
            if "Unknown resource" in str(e):
                pytest.skip(f"Resource URI not supported in this FastMCP version: {e}")
            raise

        sentry_sdk.flush()
        # Verify resource span was created
        spans = [item.payload for item in items]
        resource_spans = [
            s for s in spans if s["attributes"].get("sentry.op") == OP.MCP_SERVER
        ]
        assert len(resource_spans) == 1
        span = resource_spans[0]
        assert span["attributes"]["sentry.origin"] == "auto.ai.mcp"
        assert span["name"] == "resources/read file:///test.txt"
        assert span["attributes"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "file"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.asyncio
async def test_fastmcp_resource_async(sentry_init, capture_items, FastMCP, json_rpc):
    """Test that FastMCP async resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    mcp = FastMCP("Test Server")

    session_manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server,
        json_response=True,
    )

    app = Starlette(
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    # Try to register an async resource handler
    if hasattr(mcp, "resource"):
        items = capture_items("span")

        @mcp.resource("https://example.com/{resource}")
        async def read_url(resource: str):
            """Read a URL resource"""
            return "resource data"

        _, result = json_rpc(
            app,
            method="resources/read",
            params={
                "uri": "https://example.com/resource",
            },
            request_id="req-async-resource",
        )
        # Older FastMCP versions may not support this URI pattern
        if (
            "error" in result.json()
            and "Unknown resource" in result.json()["error"]["message"]
        ):
            pytest.skip("Resource URI not supported in this FastMCP version.")
            return

        assert "resource data" in result.json()["result"]["contents"][0]["text"]

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        spans = [
            span
            for span in spans
            if span["attributes"].get("mcp.method.name") == "resources/read"
        ]
        assert len(spans) == 1
        span = spans[0]

        assert span["attributes"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "https"


# =============================================================================
# Span Origin and Metadata Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_span_origin(
    sentry_init,
    capture_items,
    FastMCP,
    stdio,
):
    """Test that FastMCP span origin is set correctly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def test_tool(value: int) -> int:
        """Test tool for origin checking"""
        return value * 2

    items = capture_items("span")
    await stdio(
        mcp._mcp_server,
        method="tools/call",
        params={
            "name": "test_tool",
            "arguments": {"value": 21},
        },
        request_id="req-origin",
    )

    sentry_sdk.flush()

    spans = [item.payload for item in items]

    # Verify MCP span has correct origin
    mcp_spans = [s for s in spans if s["attributes"].get("sentry.op") == OP.MCP_SERVER]
    assert len(mcp_spans) == 1
    assert mcp_spans[0]["attributes"]["sentry.origin"] == "auto.ai.mcp"


# =============================================================================
# Transport Detection Tests
# =============================================================================


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_http_transport(sentry_init, capture_items, FastMCP, json_rpc):
    """Test that FastMCP correctly detects HTTP transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    mcp = FastMCP("Test Server")

    session_manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server,
        json_response=True,
    )

    app = Starlette(
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    @mcp.tool()
    def http_tool(data: str) -> dict:
        """Tool for HTTP transport test"""
        return {"processed": data.upper()}

    items = capture_items("span")

    _, result = json_rpc(
        app,
        method="tools/call",
        params={
            "name": "http_tool",
            "arguments": {"data": "test"},
        },
        request_id="req-http",
    )

    assert json.loads(result.json()["result"]["content"][0]["text"]) == {
        "processed": "TEST"
    }

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    spans = [
        span
        for span in spans
        if span["attributes"].get("mcp.method.name") == "tools/call"
    ]
    assert len(spans) == 1
    span = spans[0]

    # Check that HTTP transport is detected
    assert span["attributes"].get(SPANDATA.MCP_TRANSPORT) == "http"


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_stdio_transport(
    sentry_init,
    capture_items,
    FastMCP,
    stdio,
):
    """Test that FastMCP correctly detects stdio transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def stdio_tool(n: int) -> dict:
        """Tool for stdio transport test"""
        return {"squared": n * n}

    items = capture_items("span")
    await stdio(
        mcp._mcp_server,
        method="tools/call",
        params={
            "name": "stdio_tool",
            "arguments": {"n": 7},
        },
        request_id="req-stdio",
    )

    sentry_sdk.flush()
    # Find MCP spans
    spans = [item.payload for item in items]
    mcp_spans = [s for s in spans if s["attributes"].get("sentry.op") == OP.MCP_SERVER]

    assert len(mcp_spans) >= 1
    span = mcp_spans[0]
    # Check that stdio transport is detected

    assert span["attributes"].get(SPANDATA.MCP_TRANSPORT) == "stdio"

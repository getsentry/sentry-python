"""
Unit tests for the Sentry MCP integration with FastMCP.

This test suite verifies that Sentry's MCPIntegration properly instruments
both FastMCP implementations:
- mcp.server.fastmcp.FastMCP (FastMCP from the mcp package)
- fastmcp.FastMCP (standalone fastmcp package)

Tests focus on verifying Sentry integration behavior:
- Integration doesn't break FastMCP functionality
- Span creation when tools/prompts/resources are called through MCP protocol
- Span data accuracy (operation, description, origin, etc.)
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
import pytest
from unittest import mock

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA, OP
from sentry_sdk.integrations.mcp import MCPIntegration

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


class MockRequestContext:
    """Mock MCP request context"""

    def __init__(self, request_id=None, session_id=None, transport="stdio"):
        self.request_id = request_id
        if transport in ("http", "sse"):
            self.request = MockHTTPRequest(session_id, transport)
        else:
            self.request = None


class MockHTTPRequest:
    """Mock HTTP request for SSE/StreamableHTTP transport"""

    def __init__(self, session_id=None, transport="http"):
        self.headers = {}
        self.query_params = {}
        self.scope = {}

        if transport == "sse":
            # SSE transport uses query parameter
            if session_id:
                self.query_params["session_id"] = session_id
        else:
            # StreamableHTTP transport uses header
            if session_id:
                self.headers["mcp-session-id"] = session_id


# =============================================================================
# Tool Handler Tests - Verifying Sentry Integration
# =============================================================================


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_fastmcp_tool_sync(
    sentry_init, capture_events, FastMCP, send_default_pii, include_prompts
):
    """Test that FastMCP synchronous tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-123", transport="stdio")
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def add_numbers(a: int, b: int) -> dict:
        """Add two numbers together"""
        return {"result": a + b, "operation": "addition"}

    with start_transaction(name="fastmcp tx"):
        # Call through MCP protocol to trigger instrumentation
        result = call_tool_through_mcp(mcp, "add_numbers", {"a": 10, "b": 5})

    assert result == {"result": 15, "operation": "addition"}

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    # Verify span structure
    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["origin"] == "auto.ai.mcp"
    assert span["description"] == "tools/call add_numbers"
    assert span["data"][SPANDATA.MCP_TOOL_NAME] == "add_numbers"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "stdio"
    assert span["data"][SPANDATA.MCP_REQUEST_ID] == "req-123"

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT in span["data"]
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_fastmcp_tool_async(
    sentry_init, capture_events, FastMCP, send_default_pii, include_prompts
):
    """Test that FastMCP async tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(
            request_id="req-456", session_id="session-789", transport="http"
        )
        request_ctx.set(mock_ctx)

    @mcp.tool()
    async def multiply_numbers(x: int, y: int) -> dict:
        """Multiply two numbers together"""
        return {"result": x * y, "operation": "multiplication"}

    with start_transaction(name="fastmcp tx"):
        result = await call_tool_through_mcp_async(
            mcp, "multiply_numbers", {"x": 7, "y": 6}
        )

    assert result == {"result": 42, "operation": "multiplication"}

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    # Verify span structure
    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["origin"] == "auto.ai.mcp"
    assert span["description"] == "tools/call multiply_numbers"
    assert span["data"][SPANDATA.MCP_TOOL_NAME] == "multiply_numbers"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "http"
    assert span["data"][SPANDATA.MCP_REQUEST_ID] == "req-456"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == "session-789"

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT in span["data"]
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_tool_with_error(sentry_init, capture_events, FastMCP):
    """Test that FastMCP tool handler errors are captured properly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-error", transport="stdio")
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def failing_tool(value: int) -> int:
        """A tool that always fails"""
        raise ValueError("Tool execution failed")

    with start_transaction(name="fastmcp tx"):
        # MCP protocol may raise the error or return it as an error result
        try:
            result = call_tool_through_mcp(mcp, "failing_tool", {"value": 42})
            # If no exception raised, check if result indicates error
            if hasattr(result, "isError"):
                assert result.isError is True
        except ValueError:
            # Error was raised as expected
            pass

    # Should have transaction and error events
    assert len(events) >= 1

    # Check span was created
    tx = [e for e in events if e.get("type") == "transaction"][0]
    tool_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(tool_spans) == 1

    # Check error event was captured
    error_events = [e for e in events if e.get("level") == "error"]
    assert len(error_events) >= 1
    error_event = error_events[0]
    assert error_event["exception"]["values"][0]["type"] == "ValueError"
    assert error_event["exception"]["values"][0]["value"] == "Tool execution failed"
    # Verify span is marked with error
    assert tool_spans[0]["data"][SPANDATA.MCP_TOOL_RESULT_IS_ERROR] is True


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_multiple_tools(sentry_init, capture_events, FastMCP):
    """Test that multiple FastMCP tool calls create multiple spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-multi", transport="stdio")
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def tool_one(x: int) -> int:
        """First tool"""
        return x * 2

    @mcp.tool()
    def tool_two(y: int) -> int:
        """Second tool"""
        return y + 10

    @mcp.tool()
    def tool_three(z: int) -> int:
        """Third tool"""
        return z - 5

    with start_transaction(name="fastmcp tx"):
        result1 = call_tool_through_mcp(mcp, "tool_one", {"x": 5})
        result2 = call_tool_through_mcp(mcp, "tool_two", {"y": result1["result"]})
        result3 = call_tool_through_mcp(mcp, "tool_three", {"z": result2["result"]})

    assert result1["result"] == 10
    assert result2["result"] == 20
    assert result3["result"] == 15

    (tx,) = events
    assert tx["type"] == "transaction"

    # Verify three spans were created
    tool_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(tool_spans) == 3
    assert tool_spans[0]["data"][SPANDATA.MCP_TOOL_NAME] == "tool_one"
    assert tool_spans[1]["data"][SPANDATA.MCP_TOOL_NAME] == "tool_two"
    assert tool_spans[2]["data"][SPANDATA.MCP_TOOL_NAME] == "tool_three"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_tool_with_complex_return(sentry_init, capture_events, FastMCP):
    """Test FastMCP tool with complex nested return value"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-complex", transport="stdio")
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def get_user_data(user_id: int) -> dict:
        """Get complex user data"""
        return {
            "id": user_id,
            "name": "Alice",
            "nested": {"preferences": {"theme": "dark", "notifications": True}},
            "tags": ["admin", "verified"],
        }

    with start_transaction(name="fastmcp tx"):
        result = call_tool_through_mcp(mcp, "get_user_data", {"user_id": 123})

    assert result["id"] == 123
    assert result["name"] == "Alice"
    assert result["nested"]["preferences"]["theme"] == "dark"

    (tx,) = events
    assert tx["type"] == "transaction"

    # Verify span was created with complex data
    tool_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(tool_spans) == 1
    assert tool_spans[0]["op"] == OP.MCP_SERVER
    assert tool_spans[0]["data"][SPANDATA.MCP_TOOL_NAME] == "get_user_data"
    # Complex return value should be captured since include_prompts=True and send_default_pii=True
    assert SPANDATA.MCP_TOOL_RESULT_CONTENT in tool_spans[0]["data"]


# =============================================================================
# Prompt Handler Tests (if supported)
# =============================================================================


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
def test_fastmcp_prompt_sync(
    sentry_init, capture_events, FastMCP, send_default_pii, include_prompts
):
    """Test that FastMCP synchronous prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-prompt", transport="stdio")
        request_ctx.set(mock_ctx)

    # Try to register a prompt handler (may not be supported in all versions)
    try:
        if hasattr(mcp, "prompt"):

            @mcp.prompt()
            def code_help_prompt(language: str):
                """Get help for a programming language"""
                return [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Tell me about {language}",
                        },
                    }
                ]

            with start_transaction(name="fastmcp tx"):
                result = call_prompt_through_mcp(
                    mcp, "code_help_prompt", {"language": "python"}
                )

            assert result.messages[0].role == "user"
            assert "python" in result.messages[0].content.text.lower()

            (tx,) = events
            assert tx["type"] == "transaction"

            # Verify prompt span was created
            prompt_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
            assert len(prompt_spans) == 1
            span = prompt_spans[0]
            assert span["origin"] == "auto.ai.mcp"
            assert span["description"] == "prompts/get code_help_prompt"
            assert span["data"][SPANDATA.MCP_PROMPT_NAME] == "code_help_prompt"

            # Check PII-sensitive data
            if send_default_pii and include_prompts:
                assert SPANDATA.MCP_PROMPT_CONTENT in span["data"]
            else:
                assert SPANDATA.MCP_PROMPT_CONTENT not in span["data"]
    except AttributeError:
        # Prompt handler not supported in this version
        pytest.skip("Prompt handlers not supported in this FastMCP version")


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.asyncio
async def test_fastmcp_prompt_async(sentry_init, capture_events, FastMCP):
    """Test that FastMCP async prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(
            request_id="req-async-prompt", session_id="session-abc", transport="http"
        )
        request_ctx.set(mock_ctx)

    # Try to register an async prompt handler
    try:
        if hasattr(mcp, "prompt"):

            @mcp.prompt()
            async def async_prompt(topic: str):
                """Get async prompt for a topic"""
                return [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": f"What is {topic}?"},
                    },
                    {
                        "role": "assistant",
                        "content": {
                            "type": "text",
                            "text": "Let me explain that",
                        },
                    },
                ]

            with start_transaction(name="fastmcp tx"):
                result = await call_prompt_through_mcp_async(
                    mcp, "async_prompt", {"topic": "MCP"}
                )

            assert len(result.messages) == 2

            (tx,) = events
            assert tx["type"] == "transaction"
    except AttributeError:
        # Prompt handler not supported in this version
        pytest.skip("Prompt handlers not supported in this FastMCP version")


# =============================================================================
# Resource Handler Tests (if supported)
# =============================================================================


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_resource_sync(sentry_init, capture_events, FastMCP):
    """Test that FastMCP synchronous resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-resource", transport="stdio")
        request_ctx.set(mock_ctx)

    # Try to register a resource handler
    try:
        if hasattr(mcp, "resource"):

            @mcp.resource("file:///{path}")
            def read_file(path: str):
                """Read a file resource"""
                return "file contents"

            with start_transaction(name="fastmcp tx"):
                try:
                    result = call_resource_through_mcp(mcp, "file:///test.txt")
                except ValueError as e:
                    # Older FastMCP versions may not support this URI pattern
                    if "Unknown resource" in str(e):
                        pytest.skip(
                            f"Resource URI not supported in this FastMCP version: {e}"
                        )
                    raise

            # Resource content is returned as-is
            assert "file contents" in result.contents[0].text

            (tx,) = events
            assert tx["type"] == "transaction"

            # Verify resource span was created
            resource_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
            assert len(resource_spans) == 1
            span = resource_spans[0]
            assert span["origin"] == "auto.ai.mcp"
            assert span["description"] == "resources/read file:///test.txt"
            assert span["data"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "file"
    except (AttributeError, TypeError):
        # Resource handler not supported in this version
        pytest.skip("Resource handlers not supported in this FastMCP version")


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.asyncio
async def test_fastmcp_resource_async(sentry_init, capture_events, FastMCP):
    """Test that FastMCP async resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(
            request_id="req-async-resource", session_id="session-res", transport="http"
        )
        request_ctx.set(mock_ctx)

    # Try to register an async resource handler
    try:
        if hasattr(mcp, "resource"):

            @mcp.resource("https://example.com/{resource}")
            async def read_url(resource: str):
                """Read a URL resource"""
                return "resource data"

            with start_transaction(name="fastmcp tx"):
                try:
                    result = await call_resource_through_mcp_async(
                        mcp, "https://example.com/resource"
                    )
                except ValueError as e:
                    # Older FastMCP versions may not support this URI pattern
                    if "Unknown resource" in str(e):
                        pytest.skip(
                            f"Resource URI not supported in this FastMCP version: {e}"
                        )
                    raise

            assert "resource data" in result.contents[0].text

            (tx,) = events
            assert tx["type"] == "transaction"

            # Verify span was created
            resource_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
            assert len(resource_spans) == 1
            assert resource_spans[0]["data"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "https"
    except (AttributeError, TypeError):
        # Resource handler not supported in this version
        pytest.skip("Resource handlers not supported in this FastMCP version")


# =============================================================================
# Span Origin and Metadata Tests
# =============================================================================


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_span_origin(sentry_init, capture_events, FastMCP):
    """Test that FastMCP span origin is set correctly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-origin", transport="stdio")
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def test_tool(value: int) -> int:
        """Test tool for origin checking"""
        return value * 2

    with start_transaction(name="fastmcp tx"):
        call_tool_through_mcp(mcp, "test_tool", {"value": 21})

    (tx,) = events

    assert tx["contexts"]["trace"]["origin"] == "manual"

    # Verify MCP span has correct origin
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) == 1
    assert mcp_spans[0]["origin"] == "auto.ai.mcp"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_without_request_context(sentry_init, capture_events, FastMCP):
    """Test FastMCP handling when no request context is available"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Clear request context
    if request_ctx is not None:
        request_ctx.set(None)

    @mcp.tool()
    def test_tool_no_ctx(x: int) -> dict:
        """Test tool without context"""
        return {"result": x + 1}

    with start_transaction(name="fastmcp tx"):
        result = call_tool_through_mcp(mcp, "test_tool_no_ctx", {"x": 99})

    assert result == {"result": 100}

    # Should still create transaction even if context is missing
    (tx,) = events
    assert tx["type"] == "transaction"


# =============================================================================
# Transport Detection Tests
# =============================================================================


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_sse_transport(sentry_init, capture_events, FastMCP):
    """Test that FastMCP correctly detects SSE transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context with SSE transport
    if request_ctx is not None:
        mock_ctx = MockRequestContext(
            request_id="req-sse", session_id="session-sse-123", transport="sse"
        )
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def sse_tool(value: str) -> dict:
        """Tool for SSE transport test"""
        return {"message": f"Received: {value}"}

    with start_transaction(name="fastmcp tx"):
        result = call_tool_through_mcp(mcp, "sse_tool", {"value": "hello"})

    assert result == {"message": "Received: hello"}

    (tx,) = events

    # Find MCP spans
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) >= 1
    span = mcp_spans[0]
    # Check that SSE transport is detected
    assert span["data"].get(SPANDATA.MCP_TRANSPORT) == "sse"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_http_transport(sentry_init, capture_events, FastMCP):
    """Test that FastMCP correctly detects HTTP transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context with HTTP transport
    if request_ctx is not None:
        mock_ctx = MockRequestContext(
            request_id="req-http", session_id="session-http-456", transport="http"
        )
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def http_tool(data: str) -> dict:
        """Tool for HTTP transport test"""
        return {"processed": data.upper()}

    with start_transaction(name="fastmcp tx"):
        result = call_tool_through_mcp(mcp, "http_tool", {"data": "test"})

    assert result == {"processed": "TEST"}

    (tx,) = events

    # Find MCP spans
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) >= 1
    span = mcp_spans[0]
    # Check that HTTP transport is detected
    assert span["data"].get(SPANDATA.MCP_TRANSPORT) == "http"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_stdio_transport(sentry_init, capture_events, FastMCP):
    """Test that FastMCP correctly detects stdio transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context with stdio transport
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-stdio", transport="stdio")
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def stdio_tool(n: int) -> dict:
        """Tool for stdio transport test"""
        return {"squared": n * n}

    with start_transaction(name="fastmcp tx"):
        result = call_tool_through_mcp(mcp, "stdio_tool", {"n": 7})

    assert result == {"squared": 49}

    (tx,) = events

    # Find MCP spans
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) >= 1
    span = mcp_spans[0]
    # Check that stdio transport is detected
    assert span["data"].get(SPANDATA.MCP_TRANSPORT) == "stdio"


# =============================================================================
# Integration-specific Tests
# =============================================================================


@pytest.mark.skipif(not HAS_MCP_FASTMCP, reason="mcp.server.fastmcp not installed")
def test_mcp_fastmcp_specific_features(sentry_init, capture_events):
    """Test features specific to mcp.server.fastmcp (from mcp package)"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("MCP Package Server")

    @mcp.tool()
    def package_specific_tool(x: int) -> int:
        """Tool for mcp.server.fastmcp package"""
        return x + 100

    with start_transaction(name="mcp.server.fastmcp tx"):
        result = call_tool_through_mcp(mcp, "package_specific_tool", {"x": 50})

    assert result["result"] == 150

    (tx,) = events
    assert tx["type"] == "transaction"


@pytest.mark.skipif(
    not HAS_STANDALONE_FASTMCP, reason="standalone fastmcp not installed"
)
def test_standalone_fastmcp_specific_features(sentry_init, capture_events):
    """Test features specific to standalone fastmcp package"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    from fastmcp import FastMCP

    mcp = FastMCP("Standalone FastMCP Server")

    @mcp.tool()
    def standalone_specific_tool(message: str) -> dict:
        """Tool for standalone fastmcp package"""
        return {"echo": message, "length": len(message)}

    with start_transaction(name="standalone fastmcp tx"):
        result = call_tool_through_mcp(
            mcp, "standalone_specific_tool", {"message": "Hello FastMCP"}
        )

    assert result["echo"] == "Hello FastMCP"
    assert result["length"] == 13

    (tx,) = events
    assert tx["type"] == "transaction"


# =============================================================================
# Edge Cases and Robustness Tests
# =============================================================================


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_tool_with_no_arguments(sentry_init, capture_events, FastMCP):
    """Test FastMCP tool with no arguments"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def no_args_tool() -> str:
        """Tool that takes no arguments"""
        return "success"

    with start_transaction(name="fastmcp tx"):
        result = call_tool_through_mcp(mcp, "no_args_tool", {})

    assert result["result"] == "success"

    (tx,) = events
    assert tx["type"] == "transaction"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_tool_with_none_return(sentry_init, capture_events, FastMCP):
    """Test FastMCP tool that returns None"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def none_return_tool(action: str) -> None:
        """Tool that returns None"""
        pass

    with start_transaction(name="fastmcp tx"):
        result = call_tool_through_mcp(mcp, "none_return_tool", {"action": "log"})

    # Helper function normalizes to {"result": value} format
    assert result["result"] is None

    (tx,) = events
    assert tx["type"] == "transaction"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.asyncio
async def test_fastmcp_mixed_sync_async_tools(sentry_init, capture_events, FastMCP):
    """Test mixing sync and async tools in FastMCP"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Set up mock request context
    if request_ctx is not None:
        mock_ctx = MockRequestContext(request_id="req-mixed", transport="stdio")
        request_ctx.set(mock_ctx)

    @mcp.tool()
    def sync_add(a: int, b: int) -> int:
        """Sync addition"""
        return a + b

    @mcp.tool()
    async def async_multiply(x: int, y: int) -> int:
        """Async multiplication"""
        return x * y

    with start_transaction(name="fastmcp tx"):
        # Use async version for both since we're in an async context
        result1 = await call_tool_through_mcp_async(mcp, "sync_add", {"a": 3, "b": 4})
        result2 = await call_tool_through_mcp_async(
            mcp, "async_multiply", {"x": 5, "y": 6}
        )

    assert result1["result"] == 7
    assert result2["result"] == 30

    (tx,) = events
    assert tx["type"] == "transaction"

    # Verify both sync and async tool spans were created
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) == 2
    assert mcp_spans[0]["data"][SPANDATA.MCP_TOOL_NAME] == "sync_add"
    assert mcp_spans[1]["data"][SPANDATA.MCP_TOOL_NAME] == "async_multiply"

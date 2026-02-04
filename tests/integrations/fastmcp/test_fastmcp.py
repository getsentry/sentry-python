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

import anyio
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

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

try:
    from fastmcp.prompts import Message
except ImportError:
    Message = None


from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.applications import Starlette

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

try:
    from fastmcp import __version__ as FASTMCP_VERSION
except ImportError:
    FASTMCP_VERSION = None

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
    sentry_init, capture_events, FastMCP, send_default_pii, include_prompts, stdio
):
    """Test that FastMCP synchronous tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def add_numbers(a: int, b: int) -> dict:
        """Add two numbers together"""
        return {"result": a + b, "operation": "addition"}

    with start_transaction(name="fastmcp tx"):
        # Call through MCP protocol to trigger instrumentation
        result = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "add_numbers",
                "arguments": {"a": 10, "b": 5},
            },
            request_id="req-123",
        )

    assert json.loads(result.message.root.result["content"][0]["text"]) == {
        "result": 15,
        "operation": "addition",
    }

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
    sentry_init,
    capture_events,
    FastMCP,
    send_default_pii,
    include_prompts,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that FastMCP async tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

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

    transactions = select_transactions_with_mcp_spans(events, method_name="tools/call")
    assert len(transactions) == 1
    tx = transactions[0]
    assert len(tx["spans"]) == 1
    span = tx["spans"][0]

    assert span["op"] == OP.MCP_SERVER
    assert span["origin"] == "auto.ai.mcp"
    assert span["description"] == "tools/call multiply_numbers"
    assert span["data"][SPANDATA.MCP_TOOL_NAME] == "multiply_numbers"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "http"
    assert span["data"][SPANDATA.MCP_REQUEST_ID] == "req-456"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == session_id

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT in span["data"]
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_tool_with_error(sentry_init, capture_events, FastMCP, stdio):
    """Test that FastMCP tool handler errors are captured properly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def failing_tool(value: int) -> int:
        """A tool that always fails"""
        raise ValueError("Tool execution failed")

    with start_transaction(name="fastmcp tx"):
        result = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "failing_tool",
                "arguments": {"value": 42},
            },
            request_id="req-error",
        )
        # If no exception raised, check if result indicates error
        assert result.message.root.result["isError"] is True

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


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_multiple_tools(sentry_init, capture_events, FastMCP, stdio):
    """Test that multiple FastMCP tool calls create multiple spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

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
        result1 = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "tool_one",
                "arguments": {"x": 5},
            },
            request_id="req-multi",
        )

        result2 = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "tool_two",
                "arguments": {
                    "y": int(result1.message.root.result["content"][0]["text"])
                },
            },
            request_id="req-multi",
        )

        result3 = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "tool_three",
                "arguments": {
                    "z": int(result2.message.root.result["content"][0]["text"])
                },
            },
            request_id="req-multi",
        )

    assert result1.message.root.result["content"][0]["text"] == "10"
    assert result2.message.root.result["content"][0]["text"] == "20"
    assert result3.message.root.result["content"][0]["text"] == "15"

    (tx,) = events
    assert tx["type"] == "transaction"

    # Verify three spans were created
    tool_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(tool_spans) == 3
    assert tool_spans[0]["data"][SPANDATA.MCP_TOOL_NAME] == "tool_one"
    assert tool_spans[1]["data"][SPANDATA.MCP_TOOL_NAME] == "tool_two"
    assert tool_spans[2]["data"][SPANDATA.MCP_TOOL_NAME] == "tool_three"


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_tool_with_complex_return(
    sentry_init, capture_events, FastMCP, stdio
):
    """Test FastMCP tool with complex nested return value"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

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

    with start_transaction(name="fastmcp tx"):
        result = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "get_user_data",
                "arguments": {"user_id": 123},
            },
            request_id="req-complex",
        )

    assert json.loads(result.message.root.result["content"][0]["text"]) == {
        "id": 123,
        "name": "Alice",
        "nested": {"preferences": {"theme": "dark", "notifications": True}},
        "tags": ["admin", "verified"],
    }

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


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_fastmcp_prompt_sync(
    sentry_init, capture_events, FastMCP, send_default_pii, include_prompts, stdio
):
    """Test that FastMCP synchronous prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Try to register a prompt handler (may not be supported in all versions)
    if hasattr(mcp, "prompt"):

        @mcp.prompt()
        def code_help_prompt(language: str):
            """Get help for a programming language"""
            message = {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": f"Tell me about {language}",
                },
            }

            if FASTMCP_VERSION is not None and FASTMCP_VERSION.startswith("3"):
                message = Message(message)

            return [message]

        with start_transaction(name="fastmcp tx"):
            result = await stdio(
                mcp._mcp_server,
                method="prompts/get",
                params={
                    "name": "code_help_prompt",
                    "arguments": {"language": "python"},
                },
                request_id="req-prompt",
            )

        assert result.message.root.result["messages"][0]["role"] == "user"
        assert (
            "python"
            in result.message.root.result["messages"][0]["content"]["text"].lower()
        )

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
            assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT in span["data"]
        else:
            assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in span["data"]


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
@pytest.mark.asyncio
async def test_fastmcp_prompt_async(
    sentry_init,
    capture_events,
    FastMCP,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that FastMCP async prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

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

    # Try to register an async prompt handler
    if hasattr(mcp, "prompt"):

        @mcp.prompt()
        async def async_prompt(topic: str):
            """Get async prompt for a topic"""
            message1 = {
                "role": "user",
                "content": {"type": "text", "text": f"What is {topic}?"},
            }

            message2 = {
                "role": "assistant",
                "content": {
                    "type": "text",
                    "text": "Let me explain that",
                },
            }

            if FASTMCP_VERSION is not None and FASTMCP_VERSION.startswith("3"):
                message1 = Message(message1)
                message2 = Message(message2)

            return [message1, message2]

        _, result = json_rpc(
            app,
            method="prompts/get",
            params={
                "name": "async_prompt",
                "arguments": {"topic": "MCP"},
            },
            request_id="req-async-prompt",
        )

        assert len(result.json()["result"]["messages"]) == 2

        transactions = select_transactions_with_mcp_spans(
            events, method_name="prompts/get"
        )
        assert len(transactions) == 1


# =============================================================================
# Resource Handler Tests (if supported)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_resource_sync(sentry_init, capture_events, FastMCP, stdio):
    """Test that FastMCP synchronous resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    # Try to register a resource handler
    try:
        if hasattr(mcp, "resource"):

            @mcp.resource("file:///{path}")
            def read_file(path: str):
                """Read a file resource"""
                return "file contents"

            with start_transaction(name="fastmcp tx"):
                try:
                    result = await stdio(
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
                        pytest.skip(
                            f"Resource URI not supported in this FastMCP version: {e}"
                        )
                    raise

            # Resource content is returned as-is
            assert "file contents" in result.message.root.result["contents"][0]["text"]

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
async def test_fastmcp_resource_async(
    sentry_init,
    capture_events,
    FastMCP,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that FastMCP async resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

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
    try:
        if hasattr(mcp, "resource"):

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

            transactions = select_transactions_with_mcp_spans(
                events, method_name="resources/read"
            )
            assert len(transactions) == 1
            tx = transactions[0]
            assert len(tx["spans"]) == 1
            span = tx["spans"][0]

            assert span["data"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "https"
    except (AttributeError, TypeError):
        # Resource handler not supported in this version
        pytest.skip("Resource handlers not supported in this FastMCP version")


# =============================================================================
# Span Origin and Metadata Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_span_origin(sentry_init, capture_events, FastMCP, stdio):
    """Test that FastMCP span origin is set correctly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def test_tool(value: int) -> int:
        """Test tool for origin checking"""
        return value * 2

    with start_transaction(name="fastmcp tx"):
        await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "test_tool",
                "arguments": {"value": 21},
            },
            request_id="req-origin",
        )

    (tx,) = events

    assert tx["contexts"]["trace"]["origin"] == "manual"

    # Verify MCP span has correct origin
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) == 1
    assert mcp_spans[0]["origin"] == "auto.ai.mcp"


# =============================================================================
# Transport Detection Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_sse_transport(
    sentry_init, capture_events, FastMCP, json_rpc_sse
):
    """Test that FastMCP correctly detects SSE transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")
    sse = SseServerTransport("/messages/")

    sse_connection_closed = asyncio.Event()

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            async with anyio.create_task_group() as tg:

                async def run_server():
                    await mcp._mcp_server.run(
                        streams[0],
                        streams[1],
                        mcp._mcp_server.create_initialization_options(),
                    )

                tg.start_soon(run_server)

        sse_connection_closed.set()
        return Response()

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )

    @mcp.tool()
    def sse_tool(value: str) -> dict:
        """Tool for SSE transport test"""
        return {"message": f"Received: {value}"}

    keep_sse_alive = asyncio.Event()
    app_task, _, result = await json_rpc_sse(
        app,
        method="tools/call",
        params={
            "name": "sse_tool",
            "arguments": {"value": "hello"},
        },
        request_id="req-sse",
        keep_sse_alive=keep_sse_alive,
    )

    await sse_connection_closed.wait()
    await app_task

    assert json.loads(result["result"]["content"][0]["text"]) == {
        "message": "Received: hello"
    }

    transactions = [
        event
        for event in events
        if event["type"] == "transaction" and event["transaction"] == "/sse"
    ]
    assert len(transactions) == 1
    tx = transactions[0]

    # Find MCP spans
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) >= 1
    span = mcp_spans[0]
    # Check that SSE transport is detected
    assert span["data"].get(SPANDATA.MCP_TRANSPORT) == "sse"


@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
def test_fastmcp_http_transport(
    sentry_init,
    capture_events,
    FastMCP,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that FastMCP correctly detects HTTP transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

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

    transactions = select_transactions_with_mcp_spans(events, method_name="tools/call")
    assert len(transactions) == 1
    tx = transactions[0]
    assert len(tx["spans"]) == 1
    span = tx["spans"][0]

    # Check that HTTP transport is detected
    assert span["data"].get(SPANDATA.MCP_TRANSPORT) == "http"


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_stdio_transport(sentry_init, capture_events, FastMCP, stdio):
    """Test that FastMCP correctly detects stdio transport"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

    @mcp.tool()
    def stdio_tool(n: int) -> dict:
        """Tool for stdio transport test"""
        return {"squared": n * n}

    with start_transaction(name="fastmcp tx"):
        result = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "stdio_tool",
                "arguments": {"n": 7},
            },
            request_id="req-stdio",
        )

    assert json.loads(result.message.root.result["content"][0]["text"]) == {
        "squared": 49
    }

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


@pytest.mark.asyncio
@pytest.mark.skipif(
    not HAS_STANDALONE_FASTMCP, reason="standalone fastmcp not installed"
)
async def test_standalone_fastmcp_specific_features(sentry_init, capture_events, stdio):
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
        result = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "standalone_specific_tool",
                "arguments": {"message": "Hello FastMCP"},
            },
        )

    assert json.loads(result.message.root.result["content"][0]["text"]) == {
        "echo": "Hello FastMCP",
        "length": 13,
    }

    (tx,) = events
    assert tx["type"] == "transaction"


# =============================================================================
# Edge Cases and Robustness Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_tool_with_no_arguments(
    sentry_init, capture_events, FastMCP, stdio
):
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
        result = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "no_args_tool",
                "arguments": {},
            },
        )

    assert result.message.root.result["content"][0]["text"] == "success"

    (tx,) = events
    assert tx["type"] == "transaction"


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_tool_with_none_return(
    sentry_init, capture_events, FastMCP, stdio
):
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
        result = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "none_return_tool",
                "arguments": {"action": "log"},
            },
        )

    if (
        isinstance(mcp, StandaloneFastMCP) and FASTMCP_VERSION is not None
    ) or isinstance(mcp, MCPFastMCP):
        assert len(result.message.root.result["content"]) == 0
    else:
        assert result.message.root.result["content"] == [
            {"type": "text", "text": "None"}
        ]

    (tx,) = events
    assert tx["type"] == "transaction"


@pytest.mark.asyncio
@pytest.mark.parametrize("FastMCP", fastmcp_implementations, ids=fastmcp_ids)
async def test_fastmcp_mixed_sync_async_tools(
    sentry_init, capture_events, FastMCP, stdio
):
    """Test mixing sync and async tools in FastMCP"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mcp = FastMCP("Test Server")

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
        result1 = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "sync_add",
                "arguments": {"a": 3, "b": 4},
            },
            request_id="req-mixed",
        )
        result2 = await stdio(
            mcp._mcp_server,
            method="tools/call",
            params={
                "name": "async_multiply",
                "arguments": {"x": 5, "y": 6},
            },
            request_id="req-mixed",
        )

    assert result1.message.root.result["content"][0]["text"] == "7"
    assert result2.message.root.result["content"][0]["text"] == "30"

    (tx,) = events
    assert tx["type"] == "transaction"

    # Verify both sync and async tool spans were created
    mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]
    assert len(mcp_spans) == 2
    assert mcp_spans[0]["data"][SPANDATA.MCP_TOOL_NAME] == "sync_add"
    assert mcp_spans[1]["data"][SPANDATA.MCP_TOOL_NAME] == "async_multiply"

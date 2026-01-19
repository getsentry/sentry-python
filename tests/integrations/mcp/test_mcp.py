"""
Unit tests for the MCP (Model Context Protocol) integration.

This test suite covers:
- Tool handlers (sync and async)
- Prompt handlers (sync and async)
- Resource handlers (sync and async)
- Error handling for each handler type
- Request context data extraction (request_id, session_id, transport)
- Tool result content extraction (various formats)
- Span data validation
- Origin tracking

The tests mock the MCP server components and request context to verify
that the integration properly instruments MCP handlers with Sentry spans.
"""

import asyncio
import anyio
import pytest
import json
from unittest import mock

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import request_ctx
from mcp.types import (
    JSONRPCMessage,
    JSONRPCRequest,
    GetPromptResult,
    PromptMessage,
    TextContent,
)
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.message import SessionMessage

try:
    from mcp.server.lowlevel.server import request_ctx
except ImportError:
    request_ctx = None

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA, OP
from sentry_sdk.integrations.mcp import MCPIntegration


def get_initialization_payload(request_id: str):
    return SessionMessage(
        message=JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method="initialize",
                params={
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"},
                },
            )
        )
    )


def get_mcp_command_payload(method: str, params, request_id: str):
    return SessionMessage(
        message=JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method=method,
                params=params,
            )
        )
    )


async def stdio(server, method: str, params, request_id: str | None = None):
    if request_id is None:
        request_id = "1"  # arbitrary

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    result = {}

    async def run_server():
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )

    async def simulate_client(tg, result):
        init_request = get_initialization_payload("1")
        await read_stream_writer.send(init_request)

        await write_stream_reader.receive()

        request = get_mcp_command_payload(method, params=params, request_id=request_id)
        await read_stream_writer.send(request)

        result["response"] = await write_stream_reader.receive()

        tg.cancel_scope.cancel()

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_server)
        tg.start_soon(simulate_client, tg, result)

    return result["response"]


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


# Mock MCP types and structures
class MockURI:
    """Mock URI object for resource testing"""

    def __init__(self, uri_string):
        self.scheme = uri_string.split("://")[0] if "://" in uri_string else ""
        self.path = uri_string.split("://")[1] if "://" in uri_string else uri_string
        self._uri_string = uri_string

    def __str__(self):
        return self._uri_string


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

        if transport == "sse":
            # SSE transport uses query parameter
            if session_id:
                self.query_params["session_id"] = session_id
        else:
            # StreamableHTTP transport uses header
            if session_id:
                self.headers["mcp-session-id"] = session_id


class MockTextContent:
    """Mock TextContent object"""

    def __init__(self, text):
        self.text = text


class MockPromptMessage:
    """Mock PromptMessage object"""

    def __init__(self, role, content_text):
        self.role = role
        self.content = MockTextContent(content_text)


class MockGetPromptResult:
    """Mock GetPromptResult object"""

    def __init__(self, messages):
        self.messages = messages


def test_integration_patches_server(sentry_init):
    """Test that MCPIntegration patches the Server class"""
    # Get original methods before integration
    original_call_tool = Server.call_tool
    original_get_prompt = Server.get_prompt
    original_read_resource = Server.read_resource

    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    # After initialization, the methods should be patched
    assert Server.call_tool is not original_call_tool
    assert Server.get_prompt is not original_get_prompt
    assert Server.read_resource is not original_read_resource


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_tool_handler_stdio(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that synchronous tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def test_tool(tool_name, arguments):
        return {"result": "success", "value": 42}

    with start_transaction(name="mcp tx"):
        result = await stdio(
            server,
            method="tools/call",
            params={
                "name": "calculate",
                "arguments": {"x": 10, "y": 5},
            },
            request_id="req-123",
        )

    assert result.message.root.result["content"][0]["text"] == json.dumps(
        {"result": "success", "value": 42},
        indent=2,
    )

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "tools/call calculate"
    assert span["origin"] == "auto.ai.mcp"

    # Check span data
    assert span["data"][SPANDATA.MCP_TOOL_NAME] == "calculate"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "stdio"
    assert span["data"][SPANDATA.MCP_REQUEST_ID] == "req-123"
    assert span["data"]["mcp.request.argument.x"] == "10"
    assert span["data"]["mcp.request.argument.y"] == "5"

    # Check PII-sensitive data is only present when both flags are True
    if send_default_pii and include_prompts:
        assert span["data"][SPANDATA.MCP_TOOL_RESULT_CONTENT] == json.dumps(
            {
                "result": "success",
                "value": 42,
            }
        )
        assert span["data"][SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT] == 2
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT not in span["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_tool_handler_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that async tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    # Set up mock request context
    mock_ctx = MockRequestContext(
        request_id="req-456", session_id="session-789", transport="http"
    )
    request_ctx.set(mock_ctx)

    @server.call_tool()
    async def test_tool_async(tool_name, arguments):
        return {"status": "completed"}

    with start_transaction(name="mcp tx"):
        result = await test_tool_async("process", {"data": "test"})

    assert result == {"status": "completed"}

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "tools/call process"
    assert span["origin"] == "auto.ai.mcp"

    # Check span data
    assert span["data"][SPANDATA.MCP_TOOL_NAME] == "process"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "http"
    assert span["data"][SPANDATA.MCP_REQUEST_ID] == "req-456"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == "session-789"
    assert span["data"]["mcp.request.argument.data"] == '"test"'

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        assert span["data"][SPANDATA.MCP_TOOL_RESULT_CONTENT] == json.dumps(
            {"status": "completed"}
        )
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]


@pytest.mark.asyncio
async def test_tool_handler_with_error(sentry_init, capture_events):
    """Test that tool handler errors are captured properly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def failing_tool(tool_name, arguments):
        raise ValueError("Tool execution failed")

    with start_transaction(name="mcp tx"):
        result = await stdio(
            server,
            method="tools/call",
            params={
                "name": "bad_tool",
                "arguments": {},
            },
        )

        assert (
            result.message.root.result["content"][0]["text"] == "Tool execution failed"
        )

    # Should have error event and transaction
    assert len(events) == 2
    error_event, tx = events

    # Check error event
    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "ValueError"
    assert error_event["exception"]["values"][0]["value"] == "Tool execution failed"

    # Check transaction and span
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1
    span = tx["spans"][0]

    # Error flag should be set for tools
    assert span["data"][SPANDATA.MCP_TOOL_RESULT_IS_ERROR] is True
    assert span["status"] == "internal_error"
    assert span["tags"]["status"] == "internal_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_prompt_handler_sync(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that synchronous prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    @server.get_prompt()
    async def test_prompt(name, arguments):
        return GetPromptResult(
            description="A helpful test prompt",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text="Tell me about Python"),
                ),
            ],
        )

    with start_transaction(name="mcp tx"):
        result = await stdio(
            server,
            method="prompts/get",
            params={
                "name": "code_help",
                "arguments": {"language": "python"},
            },
            request_id="req-prompt",
        )

    assert result.message.root.result["messages"][0]["role"] == "user"
    assert (
        result.message.root.result["messages"][0]["content"]["text"]
        == "Tell me about Python"
    )

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "prompts/get code_help"
    assert span["origin"] == "auto.ai.mcp"

    # Check span data
    assert span["data"][SPANDATA.MCP_PROMPT_NAME] == "code_help"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "prompts/get"
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "stdio"
    assert span["data"][SPANDATA.MCP_REQUEST_ID] == "req-prompt"
    assert span["data"]["mcp.request.argument.name"] == '"code_help"'
    assert span["data"]["mcp.request.argument.language"] == '"python"'

    # Message count is always captured
    assert span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT] == 1

    # For single message prompts, role and content should be captured only with PII
    if send_default_pii and include_prompts:
        assert span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE] == "user"
        assert (
            span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT]
            == "Tell me about Python"
        )
    else:
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE not in span["data"]
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in span["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_prompt_handler_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that async prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    # Set up mock request context
    mock_ctx = MockRequestContext(
        request_id="req-async-prompt", session_id="session-abc", transport="http"
    )
    request_ctx.set(mock_ctx)

    @server.get_prompt()
    async def test_prompt_async(name, arguments):
        return MockGetPromptResult(
            [
                MockPromptMessage("system", "You are a helpful assistant"),
                MockPromptMessage("user", "What is MCP?"),
            ]
        )

    with start_transaction(name="mcp tx"):
        result = await test_prompt_async("mcp_info", {})

    assert len(result.messages) == 2

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "prompts/get mcp_info"

    # For multi-message prompts, count is always captured
    assert span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT] == 2
    # Role/content are never captured for multi-message prompts (even with PII)
    assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE not in span["data"]
    assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in span["data"]


@pytest.mark.asyncio
async def test_prompt_handler_with_error(sentry_init, capture_events):
    """Test that prompt handler errors are captured"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.get_prompt()
    async def failing_prompt(name, arguments):
        raise RuntimeError("Prompt not found")

    with start_transaction(name="mcp tx"):
        response = await stdio(
            server,
            method="prompts/get",
            params={
                "name": "code_help",
                "arguments": {"language": "python"},
            },
            request_id="req-prompt",
        )

    assert response.message.root.error.message == "Prompt not found"

    # Should have error event and transaction
    assert len(events) == 2
    error_event, tx = events

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_resource_handler_sync(sentry_init, capture_events):
    """Test that synchronous resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.read_resource()
    async def test_resource(uri):
        return [
            ReadResourceContents(
                content=json.dumps({"content": "file contents"}), mime_type="text/plain"
            )
        ]

    with start_transaction(name="mcp tx"):
        result = await stdio(
            server,
            method="resources/read",
            params={
                "uri": "file:///path/to/file.txt",
            },
            request_id="req-resource",
        )

    assert result.message.root.result["contents"][0]["text"] == json.dumps(
        {"content": "file contents"},
    )

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "resources/read file:///path/to/file.txt"
    assert span["origin"] == "auto.ai.mcp"

    # Check span data
    assert span["data"][SPANDATA.MCP_RESOURCE_URI] == "file:///path/to/file.txt"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "resources/read"
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "stdio"
    assert span["data"][SPANDATA.MCP_REQUEST_ID] == "req-resource"
    assert span["data"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "file"
    # Resources don't capture result content
    assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]


@pytest.mark.asyncio
async def test_resource_handler_async(sentry_init, capture_events):
    """Test that async resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    # Set up mock request context
    mock_ctx = MockRequestContext(
        request_id="req-async-resource", session_id="session-res", transport="http"
    )
    request_ctx.set(mock_ctx)

    @server.read_resource()
    async def test_resource_async(uri):
        return {"data": "resource data"}

    with start_transaction(name="mcp tx"):
        uri = MockURI("https://example.com/resource")
        result = await test_resource_async(uri)

    assert result["data"] == "resource data"

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1

    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "resources/read https://example.com/resource"

    assert span["data"][SPANDATA.MCP_RESOURCE_URI] == "https://example.com/resource"
    assert span["data"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "https"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == "session-res"


@pytest.mark.asyncio
async def test_resource_handler_with_error(sentry_init, capture_events):
    """Test that resource handler errors are captured"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.read_resource()
    async def failing_resource(uri):
        raise FileNotFoundError("Resource not found")

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="resources/read",
            params={
                "uri": "file:///missing.txt",
            },
        )

    # Should have error event and transaction
    assert len(events) == 2
    error_event, tx = events

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "FileNotFoundError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_tool_result_extraction_tuple(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test extraction of tool results from tuple format (UnstructuredContent, StructuredContent)"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def test_tool_tuple(tool_name, arguments):
        # Return CombinationContent: (UnstructuredContent, StructuredContent)
        unstructured = [MockTextContent("Result text")]
        structured = {"key": "value", "count": 5}
        return (unstructured, structured)

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="tools/call",
            params={
                "name": "calculate",
                "arguments": {"x": 10, "y": 5},
            },
        )

    (tx,) = events
    span = tx["spans"][0]

    # Should extract the structured content (second element of tuple) only with PII
    if send_default_pii and include_prompts:
        assert span["data"][SPANDATA.MCP_TOOL_RESULT_CONTENT] == json.dumps(
            {
                "key": "value",
                "count": 5,
            }
        )
        assert span["data"][SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT] == 2
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT not in span["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_tool_result_extraction_unstructured(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test extraction of tool results from UnstructuredContent (list of content blocks)"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def test_tool_unstructured(tool_name, arguments):
        # Return UnstructuredContent as list of content blocks
        return [
            MockTextContent("First part"),
            MockTextContent("Second part"),
        ]

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="tools/call",
            params={
                "name": "calculate",
                "arguments": {"x": 10, "y": 5},
            },
        )

    (tx,) = events
    span = tx["spans"][0]

    # Should extract and join text from content blocks only with PII
    if send_default_pii and include_prompts:
        assert (
            span["data"][SPANDATA.MCP_TOOL_RESULT_CONTENT] == '"First part Second part"'
        )
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]


def test_request_context_no_context(sentry_init, capture_events):
    """Test handling when no request context is available"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    # Clear request context (simulating no context available)
    # This will cause a LookupError when trying to get context
    request_ctx.set(None)

    @server.call_tool()
    def test_tool_no_ctx(tool_name, arguments):
        return {"result": "ok"}

    with start_transaction(name="mcp tx"):
        # This should work even without request context
        try:
            test_tool_no_ctx("tool", {})
        except LookupError:
            # If it raises LookupError, that's expected when context is truly missing
            pass

    # Should still create span even if context is missing
    (tx,) = events
    span = tx["spans"][0]

    # Transport defaults to "pipe" when no context
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "stdio"
    # Request ID and Session ID should not be present
    assert SPANDATA.MCP_REQUEST_ID not in span["data"]
    assert SPANDATA.MCP_SESSION_ID not in span["data"]


@pytest.mark.asyncio
async def test_span_origin(sentry_init, capture_events):
    """Test that span origin is set correctly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def test_tool(tool_name, arguments):
        return {"result": "test"}

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="tools/call",
            params={
                "name": "calculate",
                "arguments": {"x": 10, "y": 5},
            },
        )

    (tx,) = events

    assert tx["contexts"]["trace"]["origin"] == "manual"
    assert tx["spans"][0]["origin"] == "auto.ai.mcp"


@pytest.mark.asyncio
async def test_multiple_handlers(sentry_init, capture_events):
    """Test that multiple handler calls create multiple spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def tool1(tool_name, arguments):
        return {"result": "tool1"}

    @server.call_tool()
    async def tool2(tool_name, arguments):
        return {"result": "tool2"}

    @server.get_prompt()
    async def prompt1(name, arguments):
        return GetPromptResult(
            description="A test prompt",
            messages=[
                PromptMessage(
                    role="user", content=TextContent(type="text", text="Test prompt")
                )
            ],
        )

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="tools/call",
            params={
                "name": "tool_a",
                "arguments": {},
            },
        )

        await stdio(
            server,
            method="tools/call",
            params={
                "name": "tool_b",
                "arguments": {},
            },
        )

        await stdio(
            server,
            method="prompts/get",
            params={
                "name": "prompt_a",
                "arguments": {},
            },
        )

    (tx,) = events
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 3

    # Check that we have different span types
    span_ops = [span["op"] for span in tx["spans"]]
    assert all(op == OP.MCP_SERVER for op in span_ops)

    span_descriptions = [span["description"] for span in tx["spans"]]
    assert "tools/call tool_a" in span_descriptions
    assert "tools/call tool_b" in span_descriptions
    assert "prompts/get prompt_a" in span_descriptions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_prompt_with_dict_result(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test prompt handler with dict result instead of GetPromptResult object"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    @server.get_prompt()
    def test_prompt_dict(name, arguments):
        # Return dict format instead of GetPromptResult object
        return {
            "messages": [
                {"role": "user", "content": {"text": "Hello from dict"}},
            ]
        }

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="prompts/get",
            params={
                "name": "dict_prompt",
                "arguments": {},
            },
        )

    (tx,) = events
    span = tx["spans"][0]

    # Message count is always captured
    assert span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT] == 1

    # Role and content only captured with PII
    if send_default_pii and include_prompts:
        assert span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE] == "user"
        assert (
            span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT]
            == "Hello from dict"
        )
    else:
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE not in span["data"]
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in span["data"]


@pytest.mark.asyncio
@pytest.mark.skip
async def test_resource_without_protocol(sentry_init, capture_events):
    """Test resource handler with URI without protocol scheme"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.read_resource()
    def test_resource(uri):
        return {"data": "test"}

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="resources/read",
            params={
                "uri": "https://example.com/resource",
            },
        )

    (tx,) = events
    span = tx["spans"][0]

    assert span["data"][SPANDATA.MCP_RESOURCE_URI] == "simple-path"
    # No protocol should be set
    assert SPANDATA.MCP_RESOURCE_PROTOCOL not in span["data"]


@pytest.mark.asyncio
async def test_tool_with_complex_arguments(sentry_init, capture_events):
    """Test tool handler with complex nested arguments"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def test_tool_complex(tool_name, arguments):
        return {"processed": True}

    with start_transaction(name="mcp tx"):
        complex_args = {
            "nested": {"key": "value", "list": [1, 2, 3]},
            "string": "test",
            "number": 42,
        }
        await stdio(
            server,
            method="tools/call",
            params={
                "name": "complex_tool",
                "arguments": complex_args,
            },
        )

    (tx,) = events
    span = tx["spans"][0]

    # Complex arguments should be serialized
    assert span["data"]["mcp.request.argument.nested"] == json.dumps(
        {"key": "value", "list": [1, 2, 3]}
    )
    assert span["data"]["mcp.request.argument.string"] == '"test"'
    assert span["data"]["mcp.request.argument.number"] == "42"


@pytest.mark.asyncio
async def test_async_handlers_mixed(sentry_init, capture_events):
    """Test mixing sync and async handlers in the same transaction"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    # Set up mock request context
    mock_ctx = MockRequestContext(request_id="req-mixed", transport="stdio")
    request_ctx.set(mock_ctx)

    @server.call_tool()
    def sync_tool(tool_name, arguments):
        return {"type": "sync"}

    @server.call_tool()
    async def async_tool(tool_name, arguments):
        return {"type": "async"}

    with start_transaction(name="mcp tx"):
        sync_result = sync_tool("sync", {})
        async_result = await async_tool("async", {})

    assert sync_result["type"] == "sync"
    assert async_result["type"] == "async"

    (tx,) = events
    assert len(tx["spans"]) == 2

    # Both should be instrumented correctly
    assert all(span["op"] == OP.MCP_SERVER for span in tx["spans"])


def test_sse_transport_detection(sentry_init, capture_events):
    """Test that SSE transport is correctly detected via query parameter"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    # Set up mock request context with SSE transport
    mock_ctx = MockRequestContext(
        request_id="req-sse", session_id="session-sse-123", transport="sse"
    )
    request_ctx.set(mock_ctx)

    @server.call_tool()
    def test_tool(tool_name, arguments):
        return {"result": "success"}

    with start_transaction(name="mcp tx"):
        result = test_tool("sse_tool", {})

    assert result == {"result": "success"}

    (tx,) = events
    span = tx["spans"][0]

    # Check that SSE transport is detected
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "sse"
    assert span["data"][SPANDATA.NETWORK_TRANSPORT] == "tcp"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == "session-sse-123"


def test_streamable_http_transport_detection(sentry_init, capture_events):
    """Test that StreamableHTTP transport is correctly detected via header"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    # Set up mock request context with StreamableHTTP transport
    mock_ctx = MockRequestContext(
        request_id="req-http", session_id="session-http-456", transport="http"
    )
    request_ctx.set(mock_ctx)

    @server.call_tool()
    def test_tool(tool_name, arguments):
        return {"result": "success"}

    with start_transaction(name="mcp tx"):
        result = test_tool("http_tool", {})

    assert result == {"result": "success"}

    (tx,) = events
    span = tx["spans"][0]

    # Check that HTTP transport is detected
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "http"
    assert span["data"][SPANDATA.NETWORK_TRANSPORT] == "tcp"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == "session-http-456"


@pytest.mark.asyncio
async def test_stdio_transport_detection(sentry_init, capture_events):
    """Test that stdio transport is correctly detected when no HTTP request"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    async def test_tool(tool_name, arguments):
        return {"result": "success"}

    with start_transaction(name="mcp tx"):
        result = await stdio(
            server,
            method="tools/call",
            params={
                "name": "stdio_tool",
                "arguments": {},
            },
        )

    assert result.message.root.result["structuredContent"] == {"result": "success"}

    (tx,) = events
    span = tx["spans"][0]

    # Check that stdio transport is detected
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "stdio"
    assert span["data"][SPANDATA.NETWORK_TRANSPORT] == "pipe"
    # No session ID for stdio transport
    assert SPANDATA.MCP_SESSION_ID not in span["data"]

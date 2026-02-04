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

import anyio
import asyncio

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
from mcp.types import GetPromptResult, PromptMessage, TextContent
from mcp.server.lowlevel.helper_types import ReadResourceContents

try:
    from mcp.server.lowlevel.server import request_ctx
except ImportError:
    request_ctx = None

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA, OP
from sentry_sdk.integrations.mcp import MCPIntegration

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.routing import Mount, Route
from starlette.applications import Starlette
from starlette.responses import Response


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


class MockTextContent:
    """Mock TextContent object"""

    def __init__(self, text):
        self.text = text


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
    sentry_init, capture_events, send_default_pii, include_prompts, stdio
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
async def test_tool_handler_streamable_http(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that async tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
    )

    app = Starlette(
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    @server.call_tool()
    async def test_tool_async(tool_name, arguments):
        return [
            TextContent(
                type="text",
                text=json.dumps({"status": "completed"}),
            )
        ]

    session_id, result = json_rpc(
        app,
        method="tools/call",
        params={
            "name": "process",
            "arguments": {
                "data": "test",
            },
        },
        request_id="req-456",
    )
    assert result.json()["result"]["content"][0]["text"] == json.dumps(
        {"status": "completed"}
    )

    transactions = select_transactions_with_mcp_spans(events, method_name="tools/call")
    assert len(transactions) == 1
    tx = transactions[0]
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
    assert span["data"][SPANDATA.MCP_SESSION_ID] == session_id
    assert span["data"]["mcp.request.argument.data"] == '"test"'

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        # TODO: Investigate why tool result is double-serialized.
        assert span["data"][SPANDATA.MCP_TOOL_RESULT_CONTENT] == json.dumps(
            json.dumps(
                {"status": "completed"},
            )
        )
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in span["data"]


@pytest.mark.asyncio
async def test_tool_handler_with_error(sentry_init, capture_events, stdio):
    """Test that tool handler errors are captured properly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    def failing_tool(tool_name, arguments):
        raise ValueError("Tool execution failed")

    with start_transaction(name="mcp tx"):
        result = await stdio(
            server,
            method="tools/call",
            params={
                "name": "bad_tool",
                "arguments": {},
            },
            request_id="req-error",
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
async def test_prompt_handler_stdio(
    sentry_init, capture_events, send_default_pii, include_prompts, stdio
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
async def test_prompt_handler_streamable_http(
    sentry_init,
    capture_events,
    send_default_pii,
    include_prompts,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that async prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    server = Server("test-server")

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
    )

    app = Starlette(
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    @server.get_prompt()
    async def test_prompt_async(name, arguments):
        return GetPromptResult(
            description="A helpful test prompt",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text", text="You are a helpful assistant"
                    ),
                ),
                PromptMessage(
                    role="user", content=TextContent(type="text", text="What is MCP?")
                ),
            ],
        )

    _, result = json_rpc(
        app,
        method="prompts/get",
        params={
            "name": "mcp_info",
            "arguments": {},
        },
        request_id="req-async-prompt",
    )
    assert len(result.json()["result"]["messages"]) == 2

    transactions = select_transactions_with_mcp_spans(events, method_name="prompts/get")
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1
    span = tx["spans"][0]

    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "prompts/get mcp_info"

    # For multi-message prompts, count is always captured
    assert span["data"][SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT] == 2
    # Role/content are never captured for multi-message prompts (even with PII)
    assert (
        SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE not in tx["contexts"]["trace"]["data"]
    )
    assert (
        SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT
        not in tx["contexts"]["trace"]["data"]
    )


@pytest.mark.asyncio
async def test_prompt_handler_with_error(sentry_init, capture_events, stdio):
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
            request_id="req-error-prompt",
        )

    assert response.message.root.error.message == "Prompt not found"

    # Should have error event and transaction
    assert len(events) == 2
    error_event, tx = events

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_resource_handler_stdio(sentry_init, capture_events, stdio):
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
async def test_resource_handler_streamble_http(
    sentry_init,
    capture_events,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that async resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
    )

    app = Starlette(
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    @server.read_resource()
    async def test_resource_async(uri):
        return [
            ReadResourceContents(
                content=json.dumps({"data": "resource data"}), mime_type="text/plain"
            )
        ]

    session_id, result = json_rpc(
        app,
        method="resources/read",
        params={
            "uri": "https://example.com/resource",
        },
        request_id="req-async-resource",
    )

    assert result.json()["result"]["contents"][0]["text"] == json.dumps(
        {"data": "resource data"}
    )

    transactions = select_transactions_with_mcp_spans(
        events, method_name="resources/read"
    )
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1
    span = tx["spans"][0]

    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "resources/read https://example.com/resource"

    assert span["data"][SPANDATA.MCP_RESOURCE_URI] == "https://example.com/resource"
    assert span["data"][SPANDATA.MCP_RESOURCE_PROTOCOL] == "https"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == session_id


@pytest.mark.asyncio
async def test_resource_handler_with_error(sentry_init, capture_events, stdio):
    """Test that resource handler errors are captured"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.read_resource()
    def failing_resource(uri):
        raise FileNotFoundError("Resource not found")

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="resources/read",
            params={
                "uri": "file:///missing.txt",
            },
            request_id="req-error-resource",
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
    sentry_init, capture_events, send_default_pii, include_prompts, stdio
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
    def test_tool_tuple(tool_name, arguments):
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
                "arguments": {},
            },
            request_id="req-tuple",
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
    sentry_init, capture_events, send_default_pii, include_prompts, stdio
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
    def test_tool_unstructured(tool_name, arguments):
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
                "name": "text_tool",
                "arguments": {},
            },
            request_id="req-unstructured",
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


@pytest.mark.asyncio
async def test_span_origin(sentry_init, capture_events, stdio):
    """Test that span origin is set correctly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    def test_tool(tool_name, arguments):
        return {"result": "test"}

    with start_transaction(name="mcp tx"):
        await stdio(
            server,
            method="tools/call",
            params={
                "name": "calculate",
                "arguments": {"x": 10, "y": 5},
            },
            request_id="req-origin",
        )

    (tx,) = events

    assert tx["contexts"]["trace"]["origin"] == "manual"
    assert tx["spans"][0]["origin"] == "auto.ai.mcp"


@pytest.mark.asyncio
async def test_multiple_handlers(sentry_init, capture_events, stdio):
    """Test that multiple handler calls create multiple spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    def tool1(tool_name, arguments):
        return {"result": "tool1"}

    @server.call_tool()
    def tool2(tool_name, arguments):
        return {"result": "tool2"}

    @server.get_prompt()
    def prompt1(name, arguments):
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
            request_id="req-multi",
        )

        await stdio(
            server,
            method="tools/call",
            params={
                "name": "tool_b",
                "arguments": {},
            },
            request_id="req-multi",
        )

        await stdio(
            server,
            method="prompts/get",
            params={
                "name": "prompt_a",
                "arguments": {},
            },
            request_id="req-multi",
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
    sentry_init, capture_events, send_default_pii, include_prompts, stdio
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
            request_id="req-dict-prompt",
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
async def test_tool_with_complex_arguments(sentry_init, capture_events, stdio):
    """Test tool handler with complex nested arguments"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    @server.call_tool()
    def test_tool_complex(tool_name, arguments):
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
            request_id="req-complex",
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
async def test_sse_transport_detection(sentry_init, capture_events, json_rpc_sse):
    """Test that SSE transport is correctly detected via query parameter"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")
    sse = SseServerTransport("/messages/")

    sse_connection_closed = asyncio.Event()

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            async with anyio.create_task_group() as tg:

                async def run_server():
                    await server.run(
                        streams[0], streams[1], server.create_initialization_options()
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

    @server.call_tool()
    async def test_tool(tool_name, arguments):
        return {"result": "success"}

    keep_sse_alive = asyncio.Event()
    app_task, session_id, result = await json_rpc_sse(
        app,
        method="tools/call",
        params={
            "name": "sse_tool",
            "arguments": {},
        },
        request_id="req-sse",
        keep_sse_alive=keep_sse_alive,
    )

    await sse_connection_closed.wait()
    await app_task

    assert result["result"]["structuredContent"] == {"result": "success"}

    transactions = [
        event
        for event in events
        if event["type"] == "transaction" and event["transaction"] == "/sse"
    ]
    assert len(transactions) == 1
    tx = transactions[0]
    span = tx["spans"][0]

    # Check that SSE transport is detected
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "sse"
    assert span["data"][SPANDATA.NETWORK_TRANSPORT] == "tcp"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == session_id


def test_streamable_http_transport_detection(
    sentry_init,
    capture_events,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that StreamableHTTP transport is correctly detected via header"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    server = Server("test-server")

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
    )

    app = Starlette(
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    @server.call_tool()
    async def test_tool(tool_name, arguments):
        return [
            TextContent(
                type="text",
                text=json.dumps({"status": "success"}),
            )
        ]

    session_id, result = json_rpc(
        app,
        method="tools/call",
        params={
            "name": "http_tool",
            "arguments": {},
        },
        request_id="req-http",
    )
    assert result.json()["result"]["content"][0]["text"] == json.dumps(
        {"status": "success"}
    )

    transactions = select_transactions_with_mcp_spans(events, method_name="tools/call")
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx["type"] == "transaction"
    assert len(tx["spans"]) == 1
    span = tx["spans"][0]

    # Check that HTTP transport is detected
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "http"
    assert span["data"][SPANDATA.NETWORK_TRANSPORT] == "tcp"
    assert span["data"][SPANDATA.MCP_SESSION_ID] == session_id


@pytest.mark.asyncio
async def test_stdio_transport_detection(sentry_init, capture_events, stdio):
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
            request_id="req-stdio",
        )

    assert result.message.root.result["structuredContent"] == {"result": "success"}

    (tx,) = events
    span = tx["spans"][0]

    # Check that stdio transport is detected
    assert span["data"][SPANDATA.MCP_TRANSPORT] == "stdio"
    assert span["data"][SPANDATA.NETWORK_TRANSPORT] == "pipe"
    # No session ID for stdio transport
    assert SPANDATA.MCP_SESSION_ID not in span["data"]

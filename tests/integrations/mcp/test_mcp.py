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
import json
from unittest import mock

import anyio
import pytest

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import request_ctx
from mcp.types import GetPromptResult, PromptMessage, TextContent

try:
    from mcp.server.lowlevel.server import request_ctx
except ImportError:
    request_ctx = None

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount, Route

import sentry_sdk
from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.mcp import MCPIntegration


def _experiments_for(span_streaming):
    return {"trace_lifecycle": "stream" if span_streaming else "static"}


def _find_mcp_span(items, method_name=None):
    """Return the first captured MCP span item payload matching method_name."""
    for item in items:
        if item.type != "span":
            continue
        attrs = item.payload.get("attributes", {})
        if attrs.get("sentry.op") != OP.MCP_SERVER:
            continue
        if (
            method_name is not None
            and attrs.get(SPANDATA.MCP_METHOD_NAME) != method_name
        ):
            continue
        return item.payload
    return None


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
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_tool_handler_stdio(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    span_streaming,
    stdio,
):
    """Test that synchronous tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.call_tool()
    async def test_tool(tool_name, arguments):
        return {"result": "success", "value": 42}

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            result = await stdio(
                server,
                method="tools/call",
                params={
                    "name": "calculate",
                    "arguments": {"x": 10, "y": 5},
                },
                request_id="req-123",
            )
        sentry_sdk.flush()
    else:
        events = capture_events()
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

    if span_streaming:
        span = _find_mcp_span(items, method_name="tools/call")
        assert span is not None
        assert span["name"] == "tools/call calculate"
        data = span["attributes"]
        assert data["sentry.op"] == OP.MCP_SERVER
        assert data["sentry.origin"] == "auto.ai.mcp"
    else:
        (tx,) = events
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 1

        span = tx["spans"][0]
        assert span["op"] == OP.MCP_SERVER
        assert span["description"] == "tools/call calculate"
        assert span["origin"] == "auto.ai.mcp"
        data = span["data"]

    # Check span data
    assert data[SPANDATA.MCP_TOOL_NAME] == "calculate"
    assert data[SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert data[SPANDATA.MCP_TRANSPORT] == "stdio"
    assert data[SPANDATA.NETWORK_TRANSPORT] == "pipe"
    assert data[SPANDATA.MCP_REQUEST_ID] == "req-123"
    assert SPANDATA.MCP_SESSION_ID not in data
    assert data["mcp.request.argument.x"] == "10"
    assert data["mcp.request.argument.y"] == "5"

    # Check PII-sensitive data is only present when both flags are True
    if send_default_pii and include_prompts:
        assert data[SPANDATA.MCP_TOOL_RESULT_CONTENT] == json.dumps(
            {
                "result": "success",
                "value": 42,
            }
        )
        assert data[SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT] == 2
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in data
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_tool_handler_streamable_http(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    span_streaming,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that async tool handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments=_experiments_for(span_streaming),
    )

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

    if span_streaming:
        items = capture_items("span")
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
        sentry_sdk.flush()
    else:
        events = capture_events()
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

    if span_streaming:
        span = _find_mcp_span(items, method_name="tools/call")
        assert span is not None
        assert span["name"] == "tools/call process"
        data = span["attributes"]
        assert data["sentry.op"] == OP.MCP_SERVER
        assert data["sentry.origin"] == "auto.ai.mcp"
    else:
        transactions = select_transactions_with_mcp_spans(
            events, method_name="tools/call"
        )
        assert len(transactions) == 1
        tx = transactions[0]
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 1
        span = tx["spans"][0]

        assert span["op"] == OP.MCP_SERVER
        assert span["description"] == "tools/call process"
        assert span["origin"] == "auto.ai.mcp"
        data = span["data"]

    # Check span data
    assert data[SPANDATA.MCP_TOOL_NAME] == "process"
    assert data[SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert data[SPANDATA.MCP_TRANSPORT] == "http"
    assert data[SPANDATA.NETWORK_TRANSPORT] == "tcp"
    assert data[SPANDATA.MCP_REQUEST_ID] == "req-456"
    assert data[SPANDATA.MCP_SESSION_ID] == session_id
    assert data["mcp.request.argument.data"] == "test"

    # Check PII-sensitive data
    if send_default_pii and include_prompts:
        assert data[SPANDATA.MCP_TOOL_RESULT_CONTENT] == json.dumps(
            {"status": "completed"}
        )
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_tool_handler_with_error(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """Test that tool handler errors are captured properly"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.call_tool()
    def failing_tool(tool_name, arguments):
        raise ValueError("Tool execution failed")

    if span_streaming:
        items = capture_items("event", "span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            result = await stdio(
                server,
                method="tools/call",
                params={
                    "name": "bad_tool",
                    "arguments": {},
                },
                request_id="req-error",
            )
        sentry_sdk.flush()

        assert (
            result.message.root.result["content"][0]["text"] == "Tool execution failed"
        )

        error_payload = next(item.payload for item in items if item.type == "event")
        span = _find_mcp_span(items, method_name="tools/call")
        assert span is not None

        assert error_payload["level"] == "error"
        assert error_payload["exception"]["values"][0]["type"] == "ValueError"
        assert (
            error_payload["exception"]["values"][0]["value"] == "Tool execution failed"
        )

        assert span["attributes"][SPANDATA.MCP_TOOL_RESULT_IS_ERROR] is True
        assert span["status"] == "error"
    else:
        events = capture_events()
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
                result.message.root.result["content"][0]["text"]
                == "Tool execution failed"
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
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_prompt_handler_stdio(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    span_streaming,
    stdio,
):
    """Test that synchronous prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments=_experiments_for(span_streaming),
    )

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

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            result = await stdio(
                server,
                method="prompts/get",
                params={
                    "name": "code_help",
                    "arguments": {"language": "python"},
                },
                request_id="req-prompt",
            )
        sentry_sdk.flush()
    else:
        events = capture_events()
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

    if span_streaming:
        span = _find_mcp_span(items, method_name="prompts/get")
        assert span is not None
        assert span["name"] == "prompts/get code_help"
        data = span["attributes"]
        assert data["sentry.op"] == OP.MCP_SERVER
        assert data["sentry.origin"] == "auto.ai.mcp"
    else:
        (tx,) = events
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 1

        span = tx["spans"][0]
        assert span["op"] == OP.MCP_SERVER
        assert span["description"] == "prompts/get code_help"
        assert span["origin"] == "auto.ai.mcp"
        data = span["data"]

    # Check span data
    assert data[SPANDATA.MCP_PROMPT_NAME] == "code_help"
    assert data[SPANDATA.MCP_METHOD_NAME] == "prompts/get"
    assert data[SPANDATA.MCP_TRANSPORT] == "stdio"
    assert data[SPANDATA.MCP_REQUEST_ID] == "req-prompt"
    assert data["mcp.request.argument.name"] == "code_help"
    assert data["mcp.request.argument.language"] == "python"

    # Message count is always captured
    assert data[SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT] == 1

    # For single message prompts, role and content should be captured only with PII
    if send_default_pii and include_prompts:
        assert data[SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE] == "user"
        assert (
            data[SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT] == "Tell me about Python"
        )
    else:
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE not in data
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
async def test_prompt_handler_streamable_http(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    span_streaming,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that async prompt handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments=_experiments_for(span_streaming),
    )

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

    if span_streaming:
        items = capture_items("span")
        _, result = json_rpc(
            app,
            method="prompts/get",
            params={
                "name": "mcp_info",
                "arguments": {},
            },
            request_id="req-async-prompt",
        )
        sentry_sdk.flush()
    else:
        events = capture_events()
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

    if span_streaming:
        span = _find_mcp_span(items, method_name="prompts/get")
        assert span is not None
        assert span["name"] == "prompts/get mcp_info"
        data = span["attributes"]
        assert data["sentry.op"] == OP.MCP_SERVER
        assert data["sentry.origin"] == "auto.ai.mcp"
    else:
        transactions = select_transactions_with_mcp_spans(
            events, method_name="prompts/get"
        )
        assert len(transactions) == 1
        tx = transactions[0]
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 1
        span = tx["spans"][0]

        assert span["op"] == OP.MCP_SERVER
        assert span["description"] == "prompts/get mcp_info"
        data = span["data"]

    # For multi-message prompts, count is always captured
    assert data[SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT] == 2
    # Role/content are never captured for multi-message prompts (even with PII)
    assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE not in data
    assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_prompt_handler_with_error(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """Test that prompt handler errors are captured"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.get_prompt()
    async def failing_prompt(name, arguments):
        raise RuntimeError("Prompt not found")

    if span_streaming:
        items = capture_items("event", "span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            response = await stdio(
                server,
                method="prompts/get",
                params={
                    "name": "code_help",
                    "arguments": {"language": "python"},
                },
                request_id="req-error-prompt",
            )
        sentry_sdk.flush()

        assert response.message.root.error.message == "Prompt not found"

        error_payload = next(item.payload for item in items if item.type == "event")
        span = _find_mcp_span(items, method_name="prompts/get")
        assert span is not None

        assert error_payload["level"] == "error"
        assert error_payload["exception"]["values"][0]["type"] == "RuntimeError"
        assert span["status"] == "error"
        assert SPANDATA.MCP_TOOL_RESULT_IS_ERROR not in span["attributes"]
    else:
        events = capture_events()
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

        # Check transaction and span
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 1
        span = tx["spans"][0]

        assert span["status"] == "internal_error"
        assert SPANDATA.MCP_TOOL_RESULT_IS_ERROR not in span["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_resource_handler_stdio(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """Test that synchronous resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.read_resource()
    async def test_resource(uri):
        return [
            ReadResourceContents(
                content=json.dumps({"content": "file contents"}), mime_type="text/plain"
            )
        ]

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            result = await stdio(
                server,
                method="resources/read",
                params={
                    "uri": "file:///path/to/file.txt",
                },
                request_id="req-resource",
            )
        sentry_sdk.flush()
    else:
        events = capture_events()
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

    if span_streaming:
        span = _find_mcp_span(items, method_name="resources/read")
        assert span is not None
        assert span["name"] == "resources/read file:///path/to/file.txt"
        data = span["attributes"]
        assert data["sentry.op"] == OP.MCP_SERVER
        assert data["sentry.origin"] == "auto.ai.mcp"
    else:
        (tx,) = events
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 1

        span = tx["spans"][0]
        assert span["op"] == OP.MCP_SERVER
        assert span["description"] == "resources/read file:///path/to/file.txt"
        assert span["origin"] == "auto.ai.mcp"
        data = span["data"]

    # Check span data
    assert data[SPANDATA.MCP_RESOURCE_URI] == "file:///path/to/file.txt"
    assert data[SPANDATA.MCP_METHOD_NAME] == "resources/read"
    assert data[SPANDATA.MCP_TRANSPORT] == "stdio"
    assert data[SPANDATA.MCP_REQUEST_ID] == "req-resource"
    assert data[SPANDATA.MCP_RESOURCE_PROTOCOL] == "file"
    # Resources don't capture result content
    assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_resource_handler_streamable_http(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
    json_rpc,
    select_transactions_with_mcp_spans,
):
    """Test that async resource handlers create proper spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

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

    if span_streaming:
        items = capture_items("span")
        session_id, result = json_rpc(
            app,
            method="resources/read",
            params={
                "uri": "https://example.com/resource",
            },
            request_id="req-async-resource",
        )
        sentry_sdk.flush()
    else:
        events = capture_events()
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

    if span_streaming:
        span = _find_mcp_span(items, method_name="resources/read")
        assert span is not None
        assert span["name"] == "resources/read https://example.com/resource"
        data = span["attributes"]
        assert data["sentry.op"] == OP.MCP_SERVER
        assert data["sentry.origin"] == "auto.ai.mcp"
    else:
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
        data = span["data"]

    assert data[SPANDATA.MCP_RESOURCE_URI] == "https://example.com/resource"
    assert data[SPANDATA.MCP_RESOURCE_PROTOCOL] == "https"
    assert data[SPANDATA.MCP_SESSION_ID] == session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_resource_handler_with_error(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """Test that resource handler errors are captured"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.read_resource()
    def failing_resource(uri):
        raise FileNotFoundError("Resource not found")

    if span_streaming:
        items = capture_items("event", "span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            await stdio(
                server,
                method="resources/read",
                params={
                    "uri": "file:///missing.txt",
                },
                request_id="req-error-resource",
            )
        sentry_sdk.flush()

        error_payload = next(item.payload for item in items if item.type == "event")
        span = _find_mcp_span(items, method_name="resources/read")
        assert span is not None

        assert error_payload["level"] == "error"
        assert error_payload["exception"]["values"][0]["type"] == "FileNotFoundError"
        assert span["status"] == "error"
        assert SPANDATA.MCP_TOOL_RESULT_IS_ERROR not in span["attributes"]
    else:
        events = capture_events()
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

        # Check transaction and span
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 1
        span = tx["spans"][0]

        assert span["status"] == "internal_error"
        assert SPANDATA.MCP_TOOL_RESULT_IS_ERROR not in span["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_tool_result_extraction_tuple(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    span_streaming,
    stdio,
):
    """Test extraction of tool results from tuple format (UnstructuredContent, StructuredContent)"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.call_tool()
    def test_tool_tuple(tool_name, arguments):
        # Return CombinationContent: (UnstructuredContent, StructuredContent)
        unstructured = [MockTextContent("Result text")]
        structured = {"key": "value", "count": 5}
        return (unstructured, structured)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            await stdio(
                server,
                method="tools/call",
                params={
                    "name": "calculate",
                    "arguments": {},
                },
                request_id="req-tuple",
            )
        sentry_sdk.flush()

        span = _find_mcp_span(items, method_name="tools/call")
        assert span is not None
        data = span["attributes"]
    else:
        events = capture_events()
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
        data = tx["spans"][0]["data"]

    # Should extract the structured content (second element of tuple) only with PII
    if send_default_pii and include_prompts:
        assert data[SPANDATA.MCP_TOOL_RESULT_CONTENT] == json.dumps(
            {
                "key": "value",
                "count": 5,
            }
        )
        assert data[SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT] == 2
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in data
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT_COUNT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_tool_result_extraction_unstructured(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    span_streaming,
    stdio,
):
    """Test extraction of tool results from UnstructuredContent (list of content blocks)"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.call_tool()
    def test_tool_unstructured(tool_name, arguments):
        # Return UnstructuredContent as list of content blocks
        return [
            MockTextContent("First part"),
            MockTextContent("Second part"),
        ]

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            await stdio(
                server,
                method="tools/call",
                params={
                    "name": "text_tool",
                    "arguments": {},
                },
                request_id="req-unstructured",
            )
        sentry_sdk.flush()

        span = _find_mcp_span(items, method_name="tools/call")
        assert span is not None
        data = span["attributes"]
    else:
        events = capture_events()
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
        data = tx["spans"][0]["data"]

    # Should extract and join text from content blocks only with PII
    if send_default_pii and include_prompts:
        assert data[SPANDATA.MCP_TOOL_RESULT_CONTENT] == "First part Second part"
    else:
        assert SPANDATA.MCP_TOOL_RESULT_CONTENT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_multiple_handlers(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """Test that multiple handler calls create multiple spans"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

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

    if span_streaming:
        items = capture_items("span")
        tx_ctx = sentry_sdk.traces.start_span(name="mcp tx")
    else:
        events = capture_events()
        tx_ctx = start_transaction(name="mcp tx")

    with tx_ctx:
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

    if span_streaming:
        sentry_sdk.flush()
        mcp_spans = [
            item.payload
            for item in items
            if item.type == "span"
            and item.payload.get("attributes", {}).get("sentry.op") == OP.MCP_SERVER
        ]
        assert len(mcp_spans) == 3
        assert all(s["attributes"]["sentry.op"] == OP.MCP_SERVER for s in mcp_spans)
        span_names = [s["name"] for s in mcp_spans]
        assert "tools/call tool_a" in span_names
        assert "tools/call tool_b" in span_names
        assert "prompts/get prompt_a" in span_names
    else:
        (tx,) = events
        assert tx["type"] == "transaction"
        assert len(tx["spans"]) == 3

        span_ops = [span["op"] for span in tx["spans"]]
        assert all(op == OP.MCP_SERVER for op in span_ops)

        span_descriptions = [span["description"] for span in tx["spans"]]
        assert "tools/call tool_a" in span_descriptions
        assert "tools/call tool_b" in span_descriptions
        assert "prompts/get prompt_a" in span_descriptions


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (False, False)],
)
async def test_prompt_with_dict_result(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    span_streaming,
    stdio,
):
    """Test prompt handler with dict result instead of GetPromptResult object"""
    sentry_init(
        integrations=[MCPIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.get_prompt()
    def test_prompt_dict(name, arguments):
        # Return dict format instead of GetPromptResult object
        return {
            "messages": [
                {"role": "user", "content": {"text": "Hello from dict"}},
            ]
        }

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            await stdio(
                server,
                method="prompts/get",
                params={
                    "name": "dict_prompt",
                    "arguments": {},
                },
                request_id="req-dict-prompt",
            )
        sentry_sdk.flush()

        span = _find_mcp_span(items, method_name="prompts/get")
        assert span is not None
        data = span["attributes"]
    else:
        events = capture_events()
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
        data = tx["spans"][0]["data"]

    # Message count is always captured
    assert data[SPANDATA.MCP_PROMPT_RESULT_MESSAGE_COUNT] == 1

    # Role and content only captured with PII
    if send_default_pii and include_prompts:
        assert data[SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE] == "user"
        assert data[SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT] == "Hello from dict"
    else:
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_ROLE not in data
        assert SPANDATA.MCP_PROMPT_RESULT_MESSAGE_CONTENT not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_tool_with_complex_arguments(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """Test tool handler with complex nested arguments"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

    server = Server("test-server")

    @server.call_tool()
    def test_tool_complex(tool_name, arguments):
        return {"processed": True}

    complex_args = {
        "nested": {"key": "value", "list": [1, 2, 3]},
        "string": "test",
        "number": 42,
    }

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            await stdio(
                server,
                method="tools/call",
                params={
                    "name": "complex_tool",
                    "arguments": complex_args,
                },
                request_id="req-complex",
            )
        sentry_sdk.flush()

        span = _find_mcp_span(items, method_name="tools/call")
        assert span is not None
        data = span["attributes"]
    else:
        events = capture_events()
        with start_transaction(name="mcp tx"):
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
        data = tx["spans"][0]["data"]

    # Complex arguments should be serialized
    assert data["mcp.request.argument.nested"] == json.dumps(
        {"key": "value", "list": [1, 2, 3]}
    )
    assert data["mcp.request.argument.string"] == "test"
    assert data["mcp.request.argument.number"] == "42"


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_sse_transport_detection(
    sentry_init, capture_events, capture_items, span_streaming, json_rpc_sse
):
    """Test that SSE transport is correctly detected via query parameter"""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

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

    if span_streaming:
        items = capture_items("span")
    else:
        events = capture_events()

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

    if span_streaming:
        sentry_sdk.flush()
        span = _find_mcp_span(items, method_name="tools/call")
        assert span is not None
        data = span["attributes"]
    else:
        transactions = [
            event
            for event in events
            if event["type"] == "transaction" and event["transaction"] == "/sse"
        ]
        assert len(transactions) == 1
        tx = transactions[0]
        data = tx["spans"][0]["data"]

    # Check that SSE transport is detected
    assert data[SPANDATA.MCP_TRANSPORT] == "sse"
    assert data[SPANDATA.NETWORK_TRANSPORT] == "tcp"
    assert data[SPANDATA.MCP_SESSION_ID] == session_id

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

The tests drive real MCP servers over the stdio, StreamableHTTP, and SSE
transports to verify that the integration properly instruments MCP handlers
with Sentry spans.
"""

import asyncio
import json
from unittest import mock

import anyio
import pytest

from sentry_sdk.utils import package_version

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents

MCP_PACKAGE_VERSION = package_version("mcp")
IS_MCP_V2 = MCP_PACKAGE_VERSION is not None and MCP_PACKAGE_VERSION >= (2, 0, 0)

try:
    if not IS_MCP_V2:
        from mcp.server.lowlevel.server import (  # type: ignore[import-not-found]
            request_ctx,
        )
    else:
        request_ctx = None
except ImportError:
    request_ctx = None

if IS_MCP_V2:
    from mcp_types import (
        CallToolRequestParams,
        CallToolResult,
        GetPromptRequestParams,
        GetPromptResult,
        PromptMessage,
        ReadResourceRequestParams,
        ReadResourceResult,
        TextContent,
        TextResourceContents,
    )
else:
    from mcp.types import GetPromptResult, PromptMessage, TextContent

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


def _get_response(session_message):
    """Extract the JSON-RPC response from a SessionMessage (v1 or v2)."""
    msg = session_message.message
    if hasattr(msg, "root"):
        return msg.root
    return msg


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
    if not IS_MCP_V2:
        original_call_tool = Server.call_tool
        original_get_prompt = Server.get_prompt
        original_read_resource = Server.read_resource

        sentry_init(
            integrations=[MCPIntegration()],
            traces_sample_rate=1.0,
        )

        assert Server.call_tool is not original_call_tool
        assert Server.get_prompt is not original_get_prompt
        assert Server.read_resource is not original_read_resource


@pytest.mark.asyncio
@pytest.mark.skipif(
    not IS_MCP_V2, reason="Constructor handler registration is MCP v2 only"
)
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_tool_handler_constructor_registration(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """v2 handlers registered via the Server(...) constructor are instrumented.

    This is the dominant v2 registration path (used by lowlevel examples and by
    the in-tree MCPServer), distinct from add_request_handler.
    """
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

    async def test_tool(ctx, params):
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps({"result": "success"}),
                )
            ],
            structured_content={"result": "success"},
        )

    server = Server("test-server", on_call_tool=test_tool)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            await stdio(
                server,
                method="tools/call",
                params={"name": "calculate", "arguments": {"x": 10}},
                request_id="req-ctor",
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
                params={"name": "calculate", "arguments": {"x": 10}},
                request_id="req-ctor",
            )
        (tx,) = events
        assert len(tx["spans"]) == 1
        span = tx["spans"][0]
        assert span["description"] == "tools/call calculate"
        data = span["data"]

    assert data[SPANDATA.MCP_TOOL_NAME] == "calculate"
    assert data[SPANDATA.MCP_METHOD_NAME] == "tools/call"
    assert data[SPANDATA.MCP_REQUEST_ID] == "req-ctor"
    assert data["mcp.request.argument.x"] == "10"


@pytest.mark.asyncio
@pytest.mark.skipif(not IS_MCP_V2, reason="MCPServer is MCP v2 only")
async def test_mcpserver_high_level_tool_instrumented(
    sentry_init, capture_events, stdio
):
    """The in-tree high-level MCPServer wires its handlers through the lowlevel
    Server(...) constructor, so its tool calls are instrumented too."""
    from mcp.server import MCPServer

    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    mcp_server = MCPServer("test-server")

    @mcp_server.tool()
    def add(a: int, b: int) -> int:
        return a + b

    events = capture_events()
    with start_transaction(name="mcp tx"):
        await stdio(
            mcp_server._lowlevel_server,
            method="tools/call",
            params={"name": "add", "arguments": {"a": 2, "b": 3}},
            request_id="req-mcpserver",
        )

    (tx,) = events
    assert len(tx["spans"]) == 1
    span = tx["spans"][0]
    assert span["op"] == OP.MCP_SERVER
    assert span["description"] == "tools/call add"
    assert span["data"][SPANDATA.MCP_METHOD_NAME] == "tools/call"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not IS_MCP_V2, reason="Constructor handler registration is MCP v2 only"
)
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_wrapping_handler_is_idempotent(
    sentry_init, capture_events, capture_items, span_streaming, stdio
):
    """Re-registering an already-wrapped handler via add_request_handler must
    not double-wrap — invoking it should produce exactly one MCP span."""
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
        _experiments=_experiments_for(span_streaming),
    )

    async def test_tool(ctx, params):
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structured_content={"result": "ok"},
        )

    server = Server("test-server", on_call_tool=test_tool)
    entry = server.get_request_handler("tools/call")
    server.add_request_handler("tools/call", CallToolRequestParams, entry.handler)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="mcp tx"):
            await stdio(
                server,
                method="tools/call",
                params={"name": "add", "arguments": {}},
                request_id="req-idempotent",
            )
        sentry_sdk.flush()
        mcp_spans = [
            item.payload
            for item in items
            if item.type == "span"
            and item.payload.get("attributes", {}).get("sentry.op") == OP.MCP_SERVER
        ]
    else:
        events = capture_events()
        with start_transaction(name="mcp tx"):
            await stdio(
                server,
                method="tools/call",
                params={"name": "add", "arguments": {}},
                request_id="req-idempotent",
            )
        (tx,) = events
        mcp_spans = [s for s in tx["spans"] if s["op"] == OP.MCP_SERVER]

    assert len(mcp_spans) == 1


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

    if IS_MCP_V2:

        async def test_tool(ctx, params):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps({"result": "success", "value": 42}),
                    )
                ],
                structured_content={"result": "success", "value": 42},
            )

        server.add_request_handler("tools/call", CallToolRequestParams, test_tool)
    else:

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

    if IS_MCP_V2:
        assert _get_response(result).result["structuredContent"] == {
            "result": "success",
            "value": 42,
        }
    else:
        assert _get_response(result).result["content"][0]["text"] == json.dumps(
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

    if IS_MCP_V2:

        async def test_tool_async(ctx, params):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps({"status": "completed"}),
                    )
                ]
            )

        server.add_request_handler("tools/call", CallToolRequestParams, test_tool_async)
    else:

        @server.call_tool()
        async def test_tool_async(tool_name, arguments):
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"status": "completed"}),
                )
            ]

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

    if IS_MCP_V2:

        async def failing_tool(ctx, params):
            raise ValueError("Tool execution failed")

        server.add_request_handler("tools/call", CallToolRequestParams, failing_tool)
    else:

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

        resp = _get_response(result)
        if IS_MCP_V2:
            assert "Tool execution failed" in resp.error.message
        else:
            assert resp.result["content"][0]["text"] == "Tool execution failed"

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

            resp = _get_response(result)
            if IS_MCP_V2:
                assert "Tool execution failed" in resp.error.message
            else:
                assert resp.result["content"][0]["text"] == "Tool execution failed"

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

    prompt_result = GetPromptResult(
        description="A helpful test prompt",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text="Tell me about Python"),
            ),
        ],
    )

    if IS_MCP_V2:

        async def test_prompt(ctx, params):
            return prompt_result

        server.add_request_handler("prompts/get", GetPromptRequestParams, test_prompt)
    else:

        @server.get_prompt()
        async def test_prompt(name, arguments):
            return prompt_result

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

    assert _get_response(result).result["messages"][0]["role"] == "user"
    assert (
        _get_response(result).result["messages"][0]["content"]["text"]
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

    prompt_result = GetPromptResult(
        description="A helpful test prompt",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text="You are a helpful assistant"),
            ),
            PromptMessage(
                role="user", content=TextContent(type="text", text="What is MCP?")
            ),
        ],
    )

    if IS_MCP_V2:

        async def test_prompt_async(ctx, params):
            return prompt_result

        server.add_request_handler(
            "prompts/get", GetPromptRequestParams, test_prompt_async
        )
    else:

        @server.get_prompt()
        async def test_prompt_async(name, arguments):
            return prompt_result

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

    if IS_MCP_V2:

        async def failing_prompt(ctx, params):
            raise RuntimeError("Prompt not found")

        server.add_request_handler(
            "prompts/get", GetPromptRequestParams, failing_prompt
        )
    else:

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

        assert _get_response(response).error.message == "Prompt not found"

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

        assert _get_response(response).error.message == "Prompt not found"

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

    if IS_MCP_V2:

        async def test_resource(ctx, params):
            return ReadResourceResult(
                contents=[
                    TextResourceContents(
                        uri=str(params.uri),
                        text=json.dumps({"content": "file contents"}),
                        mime_type="text/plain",
                    )
                ]
            )

        server.add_request_handler(
            "resources/read", ReadResourceRequestParams, test_resource
        )
    else:

        @server.read_resource()
        async def test_resource(uri):
            return [
                ReadResourceContents(
                    content=json.dumps({"content": "file contents"}),
                    mime_type="text/plain",
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

    assert _get_response(result).result["contents"][0]["text"] == json.dumps(
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

    if IS_MCP_V2:

        async def test_resource_async(ctx, params):
            return ReadResourceResult(
                contents=[
                    TextResourceContents(
                        uri=str(params.uri),
                        text=json.dumps({"data": "resource data"}),
                        mime_type="text/plain",
                    )
                ]
            )

        server.add_request_handler(
            "resources/read", ReadResourceRequestParams, test_resource_async
        )
    else:

        @server.read_resource()
        async def test_resource_async(uri):
            return [
                ReadResourceContents(
                    content=json.dumps({"data": "resource data"}),
                    mime_type="text/plain",
                )
            ]

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

    if IS_MCP_V2:

        async def failing_resource(ctx, params):
            raise FileNotFoundError("Resource not found")

        server.add_request_handler(
            "resources/read", ReadResourceRequestParams, failing_resource
        )
    else:

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

    if IS_MCP_V2:

        async def test_tool_tuple(ctx, params):
            return CallToolResult(
                content=[TextContent(type="text", text="Result text")],
                structured_content={"key": "value", "count": 5},
            )

        server.add_request_handler("tools/call", CallToolRequestParams, test_tool_tuple)
    else:

        @server.call_tool()
        def test_tool_tuple(tool_name, arguments):
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

    if IS_MCP_V2:

        async def test_tool_unstructured(ctx, params):
            return CallToolResult(
                content=[
                    TextContent(type="text", text="First part"),
                    TextContent(type="text", text="Second part"),
                ]
            )

        server.add_request_handler(
            "tools/call", CallToolRequestParams, test_tool_unstructured
        )
    else:

        @server.call_tool()
        def test_tool_unstructured(tool_name, arguments):
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

    if IS_MCP_V2:

        async def tool_handler(ctx, params):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps({"result": params.name}),
                    )
                ]
            )

        async def prompt_handler(ctx, params):
            return GetPromptResult(
                description="A test prompt",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text="Test prompt"),
                    )
                ],
            )

        server.add_request_handler("tools/call", CallToolRequestParams, tool_handler)
        server.add_request_handler(
            "prompts/get", GetPromptRequestParams, prompt_handler
        )
    else:

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
                        role="user",
                        content=TextContent(type="text", text="Test prompt"),
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

    if IS_MCP_V2:

        async def test_prompt_dict(ctx, params):
            return GetPromptResult(
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text="Hello from dict"),
                    )
                ]
            )

        server.add_request_handler(
            "prompts/get", GetPromptRequestParams, test_prompt_dict
        )
    else:

        @server.get_prompt()
        def test_prompt_dict(name, arguments):
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

    if IS_MCP_V2:

        async def test_tool_complex(ctx, params):
            return CallToolResult(content=[TextContent(type="text", text="processed")])

        server.add_request_handler(
            "tools/call", CallToolRequestParams, test_tool_complex
        )
    else:

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
@pytest.mark.skipif(IS_MCP_V2, reason="SSE scope propagation not supported in MCP v2")
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

    if IS_MCP_V2:

        async def test_tool(ctx, params):
            return CallToolResult(
                content=[TextContent(type="text", text="success")],
                structured_content={"result": "success"},
            )

        server.add_request_handler("tools/call", CallToolRequestParams, test_tool)
    else:

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


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.skipif(not IS_MCP_V2, reason="MCP v2 SSE transport detection")
async def test_sse_transport_detection_v2(
    sentry_init, capture_events, capture_items, span_streaming, json_rpc_sse
):
    """Test that SSE transport is detected on MCP v2.

    In v2 the request context is carried by the ServerRequestContext built per
    request (request=metadata.request_context), and the SSE session id rides the
    `?session_id=` query parameter.

    Note: only transport/session detection is asserted. Sentry scope propagation
    is wired through StreamableHTTPServerTransport.handle_request, not SSE, so the
    SSE path does not get scope override.
    """
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

    async def test_tool(ctx, params):
        return CallToolResult(
            content=[TextContent(type="text", text="success")],
            structured_content={"result": "success"},
        )

    server.add_request_handler("tools/call", CallToolRequestParams, test_tool)

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
        request_id="req-sse-v2",
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
        # v2 SSE does not propagate Sentry scopes (only StreamableHTTP does), so
        # the handler runs without an active transaction and the MCP span becomes
        # its own root transaction (data lives on the trace context) rather than a
        # child span of the "/sse" request transaction. Accept either shape.
        data = None
        for event in events:
            if event.get("type") != "transaction":
                continue
            trace = event["contexts"]["trace"]
            if (
                trace.get("op") == OP.MCP_SERVER
                and trace.get("data", {}).get(SPANDATA.MCP_METHOD_NAME) == "tools/call"
            ):
                data = trace["data"]
                break
            for span in event.get("spans", []):
                if span["data"].get(SPANDATA.MCP_METHOD_NAME) == "tools/call":
                    data = span["data"]
                    break
            if data is not None:
                break
        assert data is not None

    assert data[SPANDATA.MCP_TRANSPORT] == "sse"
    assert data[SPANDATA.NETWORK_TRANSPORT] == "tcp"
    assert data[SPANDATA.MCP_SESSION_ID] == session_id


@pytest.mark.asyncio
async def test_streamable_http_scope_propagation(sentry_init, capture_events, json_rpc):
    """Errors raised inside an HTTP handler attach to the MCP transaction's trace.

    StreamableHTTPServerTransport.handle_request stashes the active isolation and
    current scopes in the ASGI scope state; the handler wrapper then re-activates
    them so the span (and any captured error) belongs to the same trace as the
    HTTP request.
    """
    sentry_init(
        integrations=[MCPIntegration()],
        traces_sample_rate=1.0,
    )

    server = Server("test-server")

    if IS_MCP_V2:

        async def failing_tool(ctx, params):
            raise ValueError("Tool execution failed")

        server.add_request_handler("tools/call", CallToolRequestParams, failing_tool)
    else:

        @server.call_tool()
        def failing_tool(tool_name, arguments):
            raise ValueError("Tool execution failed")

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

    events = capture_events()
    json_rpc(
        app,
        method="tools/call",
        params={"name": "bad_tool", "arguments": {}},
        request_id="req-scope",
    )

    error_events = [e for e in events if e.get("type") != "transaction"]
    mcp_transactions = [
        e
        for e in events
        if e.get("type") == "transaction"
        and any(
            span["data"].get(SPANDATA.MCP_METHOD_NAME) == "tools/call"
            for span in e.get("spans", [])
        )
    ]

    assert len(error_events) == 1
    assert len(mcp_transactions) == 1

    error_event = error_events[0]
    assert error_event["exception"]["values"][0]["type"] == "ValueError"

    # The captured error shares the trace of the MCP transaction, proving the
    # handler executed under the propagated request scope.
    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == mcp_transactions[0]["contexts"]["trace"]["trace_id"]
    )

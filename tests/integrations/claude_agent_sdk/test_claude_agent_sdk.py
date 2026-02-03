import pytest
from dataclasses import dataclass
from typing import List, Optional
import json

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.claude_agent_sdk import (
    ClaudeAgentSDKIntegration,
    _set_span_input_data,
    _set_span_output_data,
    _extract_text_from_message,
    _extract_tool_calls,
    _start_invoke_agent_span,
    _end_invoke_agent_span,
    _create_execute_tool_span,
    _process_tool_executions,
    _wrap_query,
    AGENT_NAME,
)
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)


@dataclass
class Options:
    model: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    system_prompt: Optional[str] = None


def make_result_message(usage=None, total_cost_usd=None):
    return ResultMessage(
        subtype="result",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=1,
        session_id="test-session",
        total_cost_usd=total_cost_usd,
        usage=usage,
    )


@pytest.fixture
def integration():
    return ClaudeAgentSDKIntegration(include_prompts=True)


@pytest.fixture
def integration_no_prompts():
    return ClaudeAgentSDKIntegration(include_prompts=False)


@pytest.fixture
def assistant_message():
    return AssistantMessage(
        content=[TextBlock(text="Hello! I'm Claude.")],
        model="claude-sonnet-4-5-20250929",
    )


@pytest.fixture
def result_message():
    return make_result_message(
        usage={
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 100,
        },
        total_cost_usd=0.005,
    )


def test_extract_text_from_assistant_message():
    message = AssistantMessage(
        content=[TextBlock(text="Hello!")],
        model="test-model",
    )
    assert _extract_text_from_message(message) == "Hello!"


def test_extract_text_from_multiple_blocks():
    message = AssistantMessage(
        content=[
            TextBlock(text="First. "),
            TextBlock(text="Second."),
        ],
        model="test-model",
    )
    assert _extract_text_from_message(message) == "First. Second."


def test_extract_text_returns_none_for_non_assistant():
    result = make_result_message(usage=None)
    assert _extract_text_from_message(result) is None


def test_extract_tool_calls():
    message = AssistantMessage(
        content=[
            TextBlock(text="Let me help."),
            ToolUseBlock(id="tool-1", name="Read", input={"path": "/test.txt"}),
        ],
        model="test-model",
    )
    tool_calls = _extract_tool_calls(message)
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "Read"
    assert tool_calls[0]["input"] == {"path": "/test.txt"}


def test_extract_tool_calls_returns_none_for_non_assistant():
    result = make_result_message(usage=None)
    assert _extract_tool_calls(result) is None


def test_set_span_input_data_basic(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        _set_span_input_data(span, "Hello", None, integration)

    assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
    assert span._data[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span._data


def test_set_span_input_data_with_options(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        options = Options(
            model="claude-opus-4-5-20251101",
            allowed_tools=["Read", "Write"],
            system_prompt="You are helpful.",
        )
        _set_span_input_data(span, "Hello", options, integration)

    assert span._data[SPANDATA.GEN_AI_REQUEST_MODEL] == "claude-opus-4-5-20251101"
    assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS in span._data
    messages = json.loads(span._data[SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are helpful."
    assert messages[1]["role"] == "user"


def test_set_span_input_data_pii_disabled(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        _set_span_input_data(span, "Hello", None, integration)

    assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span._data


def test_set_span_input_data_include_prompts_disabled(
    sentry_init, integration_no_prompts
):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        _set_span_input_data(span, "Hello", None, integration_no_prompts)

    assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span._data


def test_set_span_output_data_with_messages(
    sentry_init, integration, assistant_message, result_message
):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        messages = [assistant_message, result_message]
        _set_span_output_data(span, messages, integration)

    assert span._data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-4-5-20250929"
    assert span._data[SPANDATA.GEN_AI_REQUEST_MODEL] == "claude-sonnet-4-5-20250929"
    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span._data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span._data[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 100
    assert span._data["claude_code.total_cost_usd"] == 0.005


def test_set_span_output_data_no_usage(sentry_init, integration, assistant_message):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        result_no_usage = make_result_message(usage=None, total_cost_usd=None)
        messages = [assistant_message, result_no_usage]
        _set_span_output_data(span, messages, integration)

    assert span._data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-4-5-20250929"
    assert SPANDATA.GEN_AI_USAGE_INPUT_TOKENS not in span._data
    assert "claude_code.total_cost_usd" not in span._data


def test_set_span_output_data_with_tool_calls(sentry_init, integration, result_message):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        assistant_with_tool = AssistantMessage(
            content=[
                TextBlock(text="Let me read that."),
                ToolUseBlock(id="tool-1", name="Read", input={"path": "/test.txt"}),
            ],
            model="claude-sonnet-4-5-20250929",
        )
        messages = [assistant_with_tool, result_message]
        _set_span_output_data(span, messages, integration)

    assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in span._data


def test_set_span_output_data_pii_disabled(
    sentry_init, integration, assistant_message, result_message
):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        messages = [assistant_message, result_message]
        _set_span_output_data(span, messages, integration)

    assert span._data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-4-5-20250929"
    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span._data


def test_empty_messages_list(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        _set_span_output_data(span, [], integration)

    assert SPANDATA.GEN_AI_RESPONSE_MODEL not in span._data
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span._data


def test_integration_identifier():
    integration = ClaudeAgentSDKIntegration()
    assert integration.identifier == "claude_agent_sdk"
    assert integration.origin == "auto.ai.claude_agent_sdk"


def test_integration_include_prompts_default():
    integration = ClaudeAgentSDKIntegration()
    assert integration.include_prompts is True


def test_integration_include_prompts_false():
    integration = ClaudeAgentSDKIntegration(include_prompts=False)
    assert integration.include_prompts is False


@pytest.mark.parametrize(
    "send_default_pii,include_prompts,expect_messages",
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
def test_pii_and_prompts_matrix(
    sentry_init, send_default_pii, include_prompts, expect_messages
):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        integration = ClaudeAgentSDKIntegration(include_prompts=include_prompts)
        _set_span_input_data(span, "Test prompt", None, integration)

    if expect_messages:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span._data
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span._data


def test_model_fallback_from_response(
    sentry_init, integration, assistant_message, result_message
):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        _set_span_input_data(span, "Hello", None, integration)
        messages = [assistant_message, result_message]
        _set_span_output_data(span, messages, integration)

    assert span._data[SPANDATA.GEN_AI_REQUEST_MODEL] == "claude-sonnet-4-5-20250929"
    assert span._data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-4-5-20250929"


def test_model_from_options_preserved(
    sentry_init, integration, assistant_message, result_message
):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        options = Options(model="claude-opus-4-5-20251101")
        _set_span_input_data(span, "Hello", options, integration)
        messages = [assistant_message, result_message]
        _set_span_output_data(span, messages, integration)

    assert span._data[SPANDATA.GEN_AI_REQUEST_MODEL] == "claude-opus-4-5-20251101"
    assert span._data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-4-5-20250929"


def test_available_tools_format(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        options = Options(allowed_tools=["Read", "Write", "Bash"])
        _set_span_input_data(span, "Hello", options, integration)

    tools_data = span._data[SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
    assert isinstance(tools_data, str)
    tools = json.loads(tools_data)
    assert len(tools) == 3
    assert {"name": "Read"} in tools
    assert {"name": "Write"} in tools
    assert {"name": "Bash"} in tools


def test_cached_tokens_extraction(sentry_init, integration, assistant_message):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        result_with_cache = make_result_message(
            usage={
                "input_tokens": 5,
                "output_tokens": 15,
                "cache_read_input_tokens": 500,
            },
            total_cost_usd=0.003,
        )
        messages = [assistant_message, result_with_cache]
        _set_span_output_data(span, messages, integration)

    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5
    assert span._data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 15
    assert span._data[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 500


def test_start_invoke_agent_span_basic(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        span = _start_invoke_agent_span("Hello", None, integration)
        span.__exit__(None, None, None)

    assert span.op == OP.GEN_AI_INVOKE_AGENT
    assert span.description == f"invoke_agent {AGENT_NAME}"
    assert span._data[SPANDATA.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert span._data[SPANDATA.GEN_AI_AGENT_NAME] == AGENT_NAME
    assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span._data


def test_start_invoke_agent_span_with_system_prompt(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        options = Options(system_prompt="You are helpful.")
        span = _start_invoke_agent_span("Hello", options, integration)
        span.__exit__(None, None, None)

    messages = json.loads(span._data[SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are helpful."
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"


def test_start_invoke_agent_span_pii_disabled(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

    with start_transaction(name="test"):
        span = _start_invoke_agent_span("Hello", None, integration)
        span.__exit__(None, None, None)

    assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span._data


def test_end_invoke_agent_span_aggregates_data(
    sentry_init, integration, assistant_message, result_message
):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        span = _start_invoke_agent_span("Hello", None, integration)
        messages = [assistant_message, result_message]
        _end_invoke_agent_span(span, messages, integration)

    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span._data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span._data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "claude-sonnet-4-5-20250929"


def test_create_execute_tool_span_basic(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        tool_use = ToolUseBlock(id="tool-1", name="Read", input={"path": "/test.txt"})
        span = _create_execute_tool_span(tool_use, None, integration)
        span.finish()

    assert span.op == OP.GEN_AI_EXECUTE_TOOL
    assert span.description == "execute_tool Read"
    assert span._data[SPANDATA.GEN_AI_OPERATION_NAME] == "execute_tool"
    assert span._data[SPANDATA.GEN_AI_TOOL_NAME] == "Read"
    assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"


def test_create_execute_tool_span_with_result(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        tool_use = ToolUseBlock(id="tool-1", name="Read", input={"path": "/test.txt"})
        tool_result = ToolResultBlock(
            tool_use_id="tool-1", content="file contents here"
        )
        span = _create_execute_tool_span(tool_use, tool_result, integration)
        span.finish()

    tool_input = span._data[SPANDATA.GEN_AI_TOOL_INPUT]
    if isinstance(tool_input, str):
        tool_input = json.loads(tool_input)
    assert tool_input == {"path": "/test.txt"}
    assert span._data[SPANDATA.GEN_AI_TOOL_OUTPUT] == "file contents here"


def test_create_execute_tool_span_with_error(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        tool_use = ToolUseBlock(
            id="tool-1", name="Read", input={"path": "/nonexistent.txt"}
        )
        tool_result = ToolResultBlock(
            tool_use_id="tool-1",
            content="Error: file not found",
            is_error=True,
        )
        span = _create_execute_tool_span(tool_use, tool_result, integration)
        span.finish()

    assert span.status == "internal_error"


def test_create_execute_tool_span_pii_disabled(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )

    with start_transaction(name="test"):
        tool_use = ToolUseBlock(id="tool-1", name="Read", input={"path": "/test.txt"})
        tool_result = ToolResultBlock(tool_use_id="tool-1", content="file contents")
        span = _create_execute_tool_span(tool_use, tool_result, integration)
        span.finish()

    assert span._data[SPANDATA.GEN_AI_TOOL_NAME] == "Read"
    assert SPANDATA.GEN_AI_TOOL_INPUT not in span._data
    assert SPANDATA.GEN_AI_TOOL_OUTPUT not in span._data


def test_process_tool_executions_matches_tool_use_and_result(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        assistant_msg = AssistantMessage(
            content=[
                TextBlock(text="Let me read that."),
                ToolUseBlock(id="tool-123", name="Read", input={"path": "/test.txt"}),
                ToolResultBlock(tool_use_id="tool-123", content="file contents"),
            ],
            model="test-model",
        )
        spans = _process_tool_executions([assistant_msg], integration)

    assert len(spans) == 1
    assert spans[0].description == "execute_tool Read"


def test_process_tool_executions_multiple_tools(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        assistant_msg = AssistantMessage(
            content=[
                ToolUseBlock(id="tool-1", name="Read", input={"path": "/a.txt"}),
                ToolUseBlock(
                    id="tool-2", name="Write", input={"path": "/b.txt", "content": "x"}
                ),
                ToolResultBlock(tool_use_id="tool-1", content="content a"),
                ToolResultBlock(tool_use_id="tool-2", content="written"),
            ],
            model="test-model",
        )
        spans = _process_tool_executions([assistant_msg], integration)

    assert len(spans) == 2
    tool_descriptions = {s.description for s in spans}
    assert "execute_tool Read" in tool_descriptions
    assert "execute_tool Write" in tool_descriptions


def test_process_tool_executions_no_tools(sentry_init, integration):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        assistant_msg = AssistantMessage(
            content=[TextBlock(text="Just a text response.")],
            model="test-model",
        )
        spans = _process_tool_executions([assistant_msg], integration)

    assert len(spans) == 0


@pytest.mark.asyncio
async def test_wrap_query_creates_spans(sentry_init, capture_events):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    assistant_msg = AssistantMessage(
        content=[TextBlock(text="Hello! I can help you.")],
        model="claude-sonnet-4-5-20250929",
    )
    result_msg = make_result_message(
        usage={"input_tokens": 10, "output_tokens": 20},
        total_cost_usd=0.001,
    )

    async def mock_query(*, prompt, options=None, **kwargs):
        yield assistant_msg
        yield result_msg

    wrapped = _wrap_query(mock_query)

    with start_transaction(name="claude-agent-sdk-test"):
        messages = []
        async for msg in wrapped(prompt="Hello", options=None):
            messages.append(msg)

    assert len(messages) == 2
    assert len(events) == 1

    transaction = events[0]
    spans = transaction["spans"]

    invoke_spans = [s for s in spans if s["op"] == OP.GEN_AI_INVOKE_AGENT]
    chat_spans = [s for s in spans if s["op"] == OP.GEN_AI_CHAT]

    assert len(invoke_spans) == 1
    assert len(chat_spans) == 1

    invoke_span = invoke_spans[0]
    assert "invoke_agent" in invoke_span["description"]
    assert invoke_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert invoke_span["data"][SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20

    chat_span = chat_spans[0]
    assert chat_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert chat_span["data"][SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"


@pytest.mark.asyncio
async def test_wrap_query_with_tool_execution(sentry_init, capture_events):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    assistant_msg = AssistantMessage(
        content=[
            TextBlock(text="Let me read that file."),
            ToolUseBlock(id="tool-1", name="Read", input={"path": "/test.txt"}),
            ToolResultBlock(tool_use_id="tool-1", content="file contents"),
        ],
        model="claude-sonnet-4-5-20250929",
    )
    result_msg = make_result_message(
        usage={"input_tokens": 15, "output_tokens": 25},
        total_cost_usd=0.002,
    )

    async def mock_query(*, prompt, options=None, **kwargs):
        yield assistant_msg
        yield result_msg

    wrapped = _wrap_query(mock_query)

    with start_transaction(name="claude-agent-sdk-test"):
        async for _ in wrapped(prompt="Read /test.txt", options=None):
            pass

    assert len(events) == 1
    transaction = events[0]
    spans = transaction["spans"]

    tool_spans = [s for s in spans if s["op"] == OP.GEN_AI_EXECUTE_TOOL]
    assert len(tool_spans) == 1

    tool_span = tool_spans[0]
    assert tool_span["description"] == "execute_tool Read"
    assert tool_span["data"][SPANDATA.GEN_AI_TOOL_NAME] == "Read"


@pytest.mark.asyncio
async def test_wrap_query_with_options(sentry_init, capture_events):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    assistant_msg = AssistantMessage(
        content=[TextBlock(text="I'm helpful!")],
        model="claude-opus-4-5-20251101",
    )
    result_msg = make_result_message(
        usage={"input_tokens": 5, "output_tokens": 10},
        total_cost_usd=0.001,
    )

    async def mock_query(*, prompt, options=None, **kwargs):
        yield assistant_msg
        yield result_msg

    wrapped = _wrap_query(mock_query)
    options = Options(
        model="claude-opus-4-5-20251101",
        system_prompt="You are helpful.",
    )

    with start_transaction(name="claude-agent-sdk-test"):
        async for _ in wrapped(prompt="Hello", options=options):
            pass

    transaction = events[0]
    spans = transaction["spans"]

    chat_spans = [s for s in spans if s["op"] == OP.GEN_AI_CHAT]
    assert len(chat_spans) == 1

    chat_span = chat_spans[0]
    assert (
        chat_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "claude-opus-4-5-20251101"
    )
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in chat_span["data"]

    messages = json.loads(chat_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are helpful."


@pytest.mark.asyncio
async def test_wrap_query_pii_disabled(sentry_init, capture_events):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )
    events = capture_events()

    assistant_msg = AssistantMessage(
        content=[TextBlock(text="Response text")],
        model="claude-sonnet-4-5-20250929",
    )
    result_msg = make_result_message(
        usage={"input_tokens": 5, "output_tokens": 10},
        total_cost_usd=0.001,
    )

    async def mock_query(*, prompt, options=None, **kwargs):
        yield assistant_msg
        yield result_msg

    wrapped = _wrap_query(mock_query)

    with start_transaction(name="claude-agent-sdk-test"):
        async for _ in wrapped(prompt="Secret prompt", options=None):
            pass

    transaction = events[0]
    spans = transaction["spans"]

    chat_spans = [s for s in spans if s["op"] == OP.GEN_AI_CHAT]
    chat_span = chat_spans[0]

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_span["data"]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_span["data"]

    invoke_spans = [s for s in spans if s["op"] == OP.GEN_AI_INVOKE_AGENT]
    invoke_span = invoke_spans[0]
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10


@pytest.mark.asyncio
async def test_wrap_query_handles_exception(sentry_init, capture_events):
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    async def mock_query(*, prompt, options=None, **kwargs):
        yield AssistantMessage(content=[TextBlock(text="Starting...")], model="test")
        raise RuntimeError("API error")

    wrapped = _wrap_query(mock_query)

    with pytest.raises(RuntimeError, match="API error"):
        with start_transaction(name="claude-agent-sdk-test"):
            async for _ in wrapped(prompt="Hello", options=None):
                pass

    assert len(events) >= 1

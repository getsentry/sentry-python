import pytest
from unittest import mock
from dataclasses import dataclass, field
from typing import Any, List, Optional
import json

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.claude_agent_sdk import (
    ClaudeAgentSDKIntegration,
    _set_span_input_data,
    _set_span_output_data,
    _extract_text_from_message,
    _extract_tool_calls,
)


# Mock data classes to simulate claude_agent_sdk types
@dataclass
class MockTextBlock:
    text: str
    type: str = "text"


@dataclass
class MockToolUseBlock:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class MockAssistantMessage:
    content: List[Any]
    model: str
    error: Optional[str] = None
    parent_tool_use_id: Optional[str] = None


@dataclass
class MockResultMessage:
    subtype: str = "result"
    duration_ms: int = 1000
    duration_api_ms: int = 900
    is_error: bool = False
    num_turns: int = 1
    session_id: str = "test-session"
    total_cost_usd: Optional[float] = 0.005
    usage: Optional[dict] = None
    result: Optional[str] = None
    structured_output: Any = None


@dataclass
class MockClaudeAgentOptions:
    model: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    system_prompt: Optional[str] = None
    max_turns: Optional[int] = None
    permission_mode: Optional[str] = None


# Fixtures for mock messages
EXAMPLE_ASSISTANT_MESSAGE = MockAssistantMessage(
    content=[MockTextBlock(text="Hello! I'm Claude.")],
    model="claude-sonnet-4-5-20250929",
)

EXAMPLE_RESULT_MESSAGE = MockResultMessage(
    usage={
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_input_tokens": 100,
    },
    total_cost_usd=0.005,
)


def test_extract_text_from_assistant_message():
    """Test extracting text from an AssistantMessage."""
    # Patch the AssistantMessage and TextBlock type checks
    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
            MockTextBlock,
        ):
            message = MockAssistantMessage(
                content=[MockTextBlock(text="Hello!")],
                model="test-model",
            )
            text = _extract_text_from_message(message)
            assert text == "Hello!"


def test_extract_text_from_multiple_blocks():
    """Test extracting text from multiple text blocks."""
    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
            MockTextBlock,
        ):
            message = MockAssistantMessage(
                content=[
                    MockTextBlock(text="First. "),
                    MockTextBlock(text="Second."),
                ],
                model="test-model",
            )
            text = _extract_text_from_message(message)
            assert text == "First. Second."


def test_extract_tool_calls():
    """Test extracting tool calls from an AssistantMessage."""
    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ToolUseBlock",
            MockToolUseBlock,
        ):
            message = MockAssistantMessage(
                content=[
                    MockTextBlock(text="Let me help."),
                    MockToolUseBlock(name="Read", input={"path": "/test.txt"}),
                ],
                model="test-model",
            )
            tool_calls = _extract_tool_calls(message)
            assert len(tool_calls) == 1
            assert tool_calls[0]["name"] == "Read"
            assert tool_calls[0]["input"] == {"path": "/test.txt"}


def test_set_span_input_data_basic(sentry_init):
    """Test setting basic input data on a span."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        integration = ClaudeAgentSDKIntegration(include_prompts=True)

        _set_span_input_data(span, "Hello", None, integration)

        assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
        assert span._data[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span._data


def test_set_span_input_data_with_options(sentry_init):
    """Test setting input data with options."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        integration = ClaudeAgentSDKIntegration(include_prompts=True)

        options = MockClaudeAgentOptions(
            model="claude-opus-4-5-20251101",
            allowed_tools=["Read", "Write"],
            system_prompt="You are helpful.",
        )

        _set_span_input_data(span, "Hello", options, integration)

        assert span._data[SPANDATA.GEN_AI_REQUEST_MODEL] == "claude-opus-4-5-20251101"
        assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS in span._data
        # Check messages include system prompt
        messages = json.loads(span._data[SPANDATA.GEN_AI_REQUEST_MESSAGES])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."
        assert messages[1]["role"] == "user"


def test_set_span_input_data_pii_disabled(sentry_init):
    """Test that PII-sensitive data is not captured when PII is disabled."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,  # PII disabled
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        integration = ClaudeAgentSDKIntegration(include_prompts=True)

        _set_span_input_data(span, "Hello", None, integration)

        assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span._data


def test_set_span_input_data_include_prompts_disabled(sentry_init):
    """Test that prompts are not captured when include_prompts is False."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        integration = ClaudeAgentSDKIntegration(include_prompts=False)

        _set_span_input_data(span, "Hello", None, integration)

        assert span._data[SPANDATA.GEN_AI_SYSTEM] == "claude-agent-sdk-python"
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span._data


def test_set_span_output_data_with_messages(sentry_init):
    """Test setting output data from messages."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ResultMessage",
            MockResultMessage,
        ):
            with mock.patch(
                "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
                MockTextBlock,
            ):
                with start_transaction(name="test") as transaction:
                    span = transaction.start_child(op="test")
                    integration = ClaudeAgentSDKIntegration(include_prompts=True)

                    messages = [EXAMPLE_ASSISTANT_MESSAGE, EXAMPLE_RESULT_MESSAGE]
                    _set_span_output_data(span, messages, integration)

                    assert (
                        span._data[SPANDATA.GEN_AI_RESPONSE_MODEL]
                        == "claude-sonnet-4-5-20250929"
                    )
                    assert (
                        span._data[SPANDATA.GEN_AI_REQUEST_MODEL]
                        == "claude-sonnet-4-5-20250929"
                    )
                    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
                    assert span._data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
                    assert span._data[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
                    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 100
                    assert span._data["claude_code.total_cost_usd"] == 0.005


def test_set_span_output_data_no_usage(sentry_init):
    """Test output data when there's no usage information."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ResultMessage",
            MockResultMessage,
        ):
            with mock.patch(
                "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
                MockTextBlock,
            ):
                with start_transaction(name="test") as transaction:
                    span = transaction.start_child(op="test")
                    integration = ClaudeAgentSDKIntegration(include_prompts=True)

                    result_no_usage = MockResultMessage(usage=None, total_cost_usd=None)
                    messages = [EXAMPLE_ASSISTANT_MESSAGE, result_no_usage]
                    _set_span_output_data(span, messages, integration)

                    # Should still have model info
                    assert (
                        span._data[SPANDATA.GEN_AI_RESPONSE_MODEL]
                        == "claude-sonnet-4-5-20250929"
                    )
                    # But no token usage
                    assert SPANDATA.GEN_AI_USAGE_INPUT_TOKENS not in span._data
                    assert "claude_code.total_cost_usd" not in span._data


def test_set_span_output_data_with_tool_calls(sentry_init):
    """Test output data with tool calls."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ResultMessage",
            MockResultMessage,
        ):
            with mock.patch(
                "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
                MockTextBlock,
            ):
                with mock.patch(
                    "sentry_sdk.integrations.claude_agent_sdk.ToolUseBlock",
                    MockToolUseBlock,
                ):
                    with start_transaction(name="test") as transaction:
                        span = transaction.start_child(op="test")
                        integration = ClaudeAgentSDKIntegration(include_prompts=True)

                        assistant_with_tool = MockAssistantMessage(
                            content=[
                                MockTextBlock(text="Let me read that."),
                                MockToolUseBlock(
                                    name="Read", input={"path": "/test.txt"}
                                ),
                            ],
                            model="claude-sonnet-4-5-20250929",
                        )
                        messages = [assistant_with_tool, EXAMPLE_RESULT_MESSAGE]
                        _set_span_output_data(span, messages, integration)

                        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in span._data


def test_set_span_output_data_pii_disabled(sentry_init):
    """Test that response text is not captured when PII is disabled."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=False,  # PII disabled
    )

    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ResultMessage",
            MockResultMessage,
        ):
            with mock.patch(
                "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
                MockTextBlock,
            ):
                with start_transaction(name="test") as transaction:
                    span = transaction.start_child(op="test")
                    integration = ClaudeAgentSDKIntegration(include_prompts=True)

                    messages = [EXAMPLE_ASSISTANT_MESSAGE, EXAMPLE_RESULT_MESSAGE]
                    _set_span_output_data(span, messages, integration)

                    # Should have model and tokens
                    assert (
                        span._data[SPANDATA.GEN_AI_RESPONSE_MODEL]
                        == "claude-sonnet-4-5-20250929"
                    )
                    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
                    # But not response text
                    assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span._data


def test_integration_identifier():
    """Test that the integration has the correct identifier."""
    integration = ClaudeAgentSDKIntegration()
    assert integration.identifier == "claude_agent_sdk"
    assert integration.origin == "auto.ai.claude_agent_sdk"


def test_integration_include_prompts_default():
    """Test that include_prompts defaults to True."""
    integration = ClaudeAgentSDKIntegration()
    assert integration.include_prompts is True


def test_integration_include_prompts_false():
    """Test setting include_prompts to False."""
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
    """Test the matrix of PII and include_prompts settings."""
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


def test_model_fallback_from_response(sentry_init):
    """Test that request model falls back to response model if not set."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ResultMessage",
            MockResultMessage,
        ):
            with mock.patch(
                "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
                MockTextBlock,
            ):
                with start_transaction(name="test") as transaction:
                    span = transaction.start_child(op="test")
                    integration = ClaudeAgentSDKIntegration(include_prompts=True)

                    # Don't set request model in input
                    _set_span_input_data(span, "Hello", None, integration)

                    # Now set output with response model
                    messages = [EXAMPLE_ASSISTANT_MESSAGE, EXAMPLE_RESULT_MESSAGE]
                    _set_span_output_data(span, messages, integration)

                    # Request model should be set from response model
                    assert (
                        span._data[SPANDATA.GEN_AI_REQUEST_MODEL]
                        == "claude-sonnet-4-5-20250929"
                    )
                    assert (
                        span._data[SPANDATA.GEN_AI_RESPONSE_MODEL]
                        == "claude-sonnet-4-5-20250929"
                    )


def test_model_from_options_preserved(sentry_init):
    """Test that request model from options is preserved."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ResultMessage",
            MockResultMessage,
        ):
            with mock.patch(
                "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
                MockTextBlock,
            ):
                with start_transaction(name="test") as transaction:
                    span = transaction.start_child(op="test")
                    integration = ClaudeAgentSDKIntegration(include_prompts=True)

                    # Set request model from options
                    options = MockClaudeAgentOptions(model="claude-opus-4-5-20251101")
                    _set_span_input_data(span, "Hello", options, integration)

                    # Now set output with different response model
                    messages = [EXAMPLE_ASSISTANT_MESSAGE, EXAMPLE_RESULT_MESSAGE]
                    _set_span_output_data(span, messages, integration)

                    # Request model should be preserved from options
                    assert (
                        span._data[SPANDATA.GEN_AI_REQUEST_MODEL]
                        == "claude-opus-4-5-20251101"
                    )
                    # Response model should be from response
                    assert (
                        span._data[SPANDATA.GEN_AI_RESPONSE_MODEL]
                        == "claude-sonnet-4-5-20250929"
                    )


def test_available_tools_format(sentry_init):
    """Test that available tools are formatted correctly."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        integration = ClaudeAgentSDKIntegration(include_prompts=True)

        options = MockClaudeAgentOptions(allowed_tools=["Read", "Write", "Bash"])
        _set_span_input_data(span, "Hello", options, integration)

        tools_data = span._data[SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
        # Should be a JSON string of tool objects
        assert isinstance(tools_data, str)
        tools = json.loads(tools_data)
        assert len(tools) == 3
        assert {"name": "Read"} in tools
        assert {"name": "Write"} in tools
        assert {"name": "Bash"} in tools


def test_cached_tokens_extraction(sentry_init):
    """Test extraction of cached input tokens."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with mock.patch(
        "sentry_sdk.integrations.claude_agent_sdk.AssistantMessage",
        MockAssistantMessage,
    ):
        with mock.patch(
            "sentry_sdk.integrations.claude_agent_sdk.ResultMessage",
            MockResultMessage,
        ):
            with mock.patch(
                "sentry_sdk.integrations.claude_agent_sdk.TextBlock",
                MockTextBlock,
            ):
                with start_transaction(name="test") as transaction:
                    span = transaction.start_child(op="test")
                    integration = ClaudeAgentSDKIntegration(include_prompts=True)

                    result_with_cache = MockResultMessage(
                        usage={
                            "input_tokens": 5,
                            "output_tokens": 15,
                            "cache_read_input_tokens": 500,
                        },
                        total_cost_usd=0.003,
                    )

                    messages = [EXAMPLE_ASSISTANT_MESSAGE, result_with_cache]
                    _set_span_output_data(span, messages, integration)

                    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5
                    assert span._data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 15
                    assert span._data[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
                    assert span._data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 500


def test_empty_messages_list(sentry_init):
    """Test handling of empty messages list."""
    sentry_init(
        integrations=[ClaudeAgentSDKIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test") as transaction:
        span = transaction.start_child(op="test")
        integration = ClaudeAgentSDKIntegration(include_prompts=True)

        _set_span_output_data(span, [], integration)

        # Should not crash and should not have response data
        assert SPANDATA.GEN_AI_RESPONSE_MODEL not in span._data
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span._data

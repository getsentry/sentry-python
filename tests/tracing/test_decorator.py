import inspect
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.consts import SPANTEMPLATE
from sentry_sdk.tracing import trace
from sentry_sdk.tracing_utils import create_span_decorator
from sentry_sdk.utils import logger
from tests.conftest import patch_start_tracing_child


def my_example_function():
    return "return_of_sync_function"


async def my_async_example_function():
    return "return_of_async_function"


@pytest.mark.forked
def test_trace_decorator():
    with patch_start_tracing_child() as fake_start_child:
        result = my_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_sync_function"

        start_child_span_decorator = create_span_decorator()
        result2 = start_child_span_decorator(my_example_function)()
        fake_start_child.assert_called_once_with(
            op="function", name="test_decorator.my_example_function"
        )
        assert result2 == "return_of_sync_function"


def test_trace_decorator_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "debug", mock.Mock()) as fake_debug:
            result = my_example_function()
            fake_debug.assert_not_called()
            assert result == "return_of_sync_function"

            start_child_span_decorator = create_span_decorator()
            result2 = start_child_span_decorator(my_example_function)()
            fake_debug.assert_called_once_with(
                "Cannot create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator.my_example_function",
            )
            assert result2 == "return_of_sync_function"


@pytest.mark.forked
@pytest.mark.asyncio
async def test_trace_decorator_async():
    with patch_start_tracing_child() as fake_start_child:
        result = await my_async_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_async_function"

        start_child_span_decorator = create_span_decorator()
        result2 = await start_child_span_decorator(my_async_example_function)()
        fake_start_child.assert_called_once_with(
            op="function",
            name="test_decorator.my_async_example_function",
        )
        assert result2 == "return_of_async_function"


@pytest.mark.asyncio
async def test_trace_decorator_async_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "debug", mock.Mock()) as fake_debug:
            result = await my_async_example_function()
            fake_debug.assert_not_called()
            assert result == "return_of_async_function"

            start_child_span_decorator = create_span_decorator()
            result2 = await start_child_span_decorator(my_async_example_function)()
            fake_debug.assert_called_once_with(
                "Cannot create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator.my_async_example_function",
            )
            assert result2 == "return_of_async_function"


def test_functions_to_trace_signature_unchanged_sync(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
    )

    def _some_function(a, b, c):
        pass

    @trace
    def _some_function_traced(a, b, c):
        pass

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced, 1, 2, 3
    )


@pytest.mark.asyncio
async def test_functions_to_trace_signature_unchanged_async(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
    )

    async def _some_function(a, b, c):
        pass

    @trace
    async def _some_function_traced(a, b, c):
        pass

    assert inspect.getcallargs(_some_function, 1, 2, 3) == inspect.getcallargs(
        _some_function_traced, 1, 2, 3
    )


def test_span_templates(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_TOOL)
    def my_tool(arg1, arg2):
        mock_usage = mock.Mock()
        mock_usage.input_tokens = 11
        mock_usage.output_tokens = 22
        mock_usage.total_tokens = 33

        return {
            "output": "my_tool_result",
            "usage": mock_usage,
        }

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_CHAT)
    def my_chat(model=None, **kwargs):
        mock_result = mock.Mock()
        mock_result.content = "my_chat_result"
        mock_result.usage = {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        }
        mock_result.model = f"{model}-v123"

        return mock_result

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_AGENT)
    def my_agent():
        my_tool(1, 2)
        my_chat(
            model="my-gpt-4o-mini",
            prompt="What is the weather in Tokyo?",
            system_prompt="You are a helpful assistant that can answer questions about the weather.",
            max_tokens=100,
            temperature=0.5,
            top_p=0.9,
            top_k=40,
            frequency_penalty=1.0,
            presence_penalty=2.0,
        )

    with sentry_sdk.start_transaction(name="test-transaction"):
        my_agent()

    (event,) = events
    (agent_span, tool_span, chat_span) = event["spans"]

    assert agent_span["op"] == "gen_ai.invoke_agent"
    assert (
        agent_span["description"]
        == "invoke_agent test_decorator.test_span_templates.<locals>.my_agent"
    )
    assert agent_span["data"] == {
        "gen_ai.agent.name": "test_decorator.test_span_templates.<locals>.my_agent",
        "gen_ai.operation.name": "invoke_agent",
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    assert tool_span["op"] == "gen_ai.execute_tool"
    assert (
        tool_span["description"]
        == "execute_tool test_decorator.test_span_templates.<locals>.my_tool"
    )
    assert tool_span["data"] == {
        "gen_ai.tool.name": "test_decorator.test_span_templates.<locals>.my_tool",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.output_tokens": 22,
        "gen_ai.usage.total_tokens": 33,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    assert chat_span["op"] == "gen_ai.chat"
    assert chat_span["description"] == "chat my-gpt-4o-mini"
    assert chat_span["data"] == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.frequency_penalty": 1.0,
        "gen_ai.request.max_tokens": 100,
        "gen_ai.request.messages": "[{'role': 'user', 'content': 'What is the weather in Tokyo?'}, {'role': 'system', 'content': 'You are a helpful assistant that can answer questions about the weather.'}]",
        "gen_ai.request.model": "my-gpt-4o-mini",
        "gen_ai.request.presence_penalty": 2.0,
        "gen_ai.request.temperature": 0.5,
        "gen_ai.request.top_k": 40,
        "gen_ai.request.top_p": 0.9,
        "gen_ai.response.model": "my-gpt-4o-mini-v123",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 20,
        "gen_ai.usage.total_tokens": 30,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

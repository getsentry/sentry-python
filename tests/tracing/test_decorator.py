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


def test_span_templates_ai_dicts(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_TOOL)
    def my_tool(arg1, arg2):
        """This is a tool function."""
        return {
            "output": "my_tool_result",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_CHAT)
    def my_chat(model=None, **kwargs):
        return {
            "content": "my_chat_result",
            "usage": {
                "input_tokens": 11,
                "output_tokens": 22,
                "total_tokens": 33,
            },
            "model": f"{model}-v123",
        }

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
        == "invoke_agent test_decorator.test_span_templates_ai_dicts.<locals>.my_agent"
    )
    assert agent_span["data"] == {
        "gen_ai.agent.name": "test_decorator.test_span_templates_ai_dicts.<locals>.my_agent",
        "gen_ai.operation.name": "invoke_agent",
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    assert tool_span["op"] == "gen_ai.execute_tool"
    assert (
        tool_span["description"]
        == "execute_tool test_decorator.test_span_templates_ai_dicts.<locals>.my_tool"
    )
    assert tool_span["data"] == {
        "gen_ai.tool.name": "test_decorator.test_span_templates_ai_dicts.<locals>.my_tool",
        "gen_ai.tool.description": "This is a tool function.",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 20,
        "gen_ai.usage.total_tokens": 30,
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
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.output_tokens": 22,
        "gen_ai.usage.total_tokens": 33,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }


def test_span_templates_ai_objects(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_TOOL)
    def my_tool(arg1, arg2):
        """This is a tool function."""
        mock_usage = mock.Mock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_usage.total_tokens = 30

        mock_result = mock.Mock()
        mock_result.output = "my_tool_result"
        mock_result.usage = mock_usage

        return mock_result

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_CHAT)
    def my_chat(model=None, **kwargs):
        mock_result = mock.Mock()
        mock_result.content = "my_chat_result"
        mock_result.usage = mock.Mock(
            input_tokens=11,
            output_tokens=22,
            total_tokens=33,
        )
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
        == "invoke_agent test_decorator.test_span_templates_ai_objects.<locals>.my_agent"
    )
    assert agent_span["data"] == {
        "gen_ai.agent.name": "test_decorator.test_span_templates_ai_objects.<locals>.my_agent",
        "gen_ai.operation.name": "invoke_agent",
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }

    assert tool_span["op"] == "gen_ai.execute_tool"
    assert (
        tool_span["description"]
        == "execute_tool test_decorator.test_span_templates_ai_objects.<locals>.my_tool"
    )
    assert tool_span["data"] == {
        "gen_ai.tool.name": "test_decorator.test_span_templates_ai_objects.<locals>.my_tool",
        "gen_ai.tool.description": "This is a tool function.",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 20,
        "gen_ai.usage.total_tokens": 30,
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
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.output_tokens": 22,
        "gen_ai.usage.total_tokens": 33,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_span_templates_ai_pii(sentry_init, capture_events, send_default_pii):
    sentry_init(traces_sample_rate=1.0, send_default_pii=send_default_pii)
    events = capture_events()

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_TOOL)
    def my_tool(arg1, arg2, **kwargs):
        """This is a tool function."""
        return "tool_output"

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_CHAT)
    def my_chat(model=None, **kwargs):
        return "chat_output"

    @sentry_sdk.trace(template=SPANTEMPLATE.AI_AGENT)
    def my_agent(*args, **kwargs):
        my_tool(1, 2, tool_arg1="3", tool_arg2="4")
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
        return "agent_output"

    with sentry_sdk.start_transaction(name="test-transaction"):
        my_agent(22, 33, arg1=44, arg2=55)

    (event,) = events
    (_, tool_span, _) = event["spans"]

    if send_default_pii:
        assert (
            tool_span["data"]["gen_ai.tool.input"]
            == "{'args': (1, 2), 'kwargs': {'tool_arg1': '3', 'tool_arg2': '4'}}"
        )
        assert tool_span["data"]["gen_ai.tool.output"] == "'tool_output'"
    else:
        assert "gen_ai.tool.input" not in tool_span["data"]
        assert "gen_ai.tool.output" not in tool_span["data"]

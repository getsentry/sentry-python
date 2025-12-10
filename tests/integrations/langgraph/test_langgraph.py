import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA, OP


def mock_langgraph_imports():
    """Mock langgraph modules to prevent import errors."""
    mock_state_graph = MagicMock()
    mock_pregel = MagicMock()

    langgraph_graph_mock = MagicMock()
    langgraph_graph_mock.StateGraph = mock_state_graph

    langgraph_pregel_mock = MagicMock()
    langgraph_pregel_mock.Pregel = mock_pregel

    sys.modules["langgraph"] = MagicMock()
    sys.modules["langgraph.graph"] = langgraph_graph_mock
    sys.modules["langgraph.pregel"] = langgraph_pregel_mock

    return mock_state_graph, mock_pregel


mock_state_graph, mock_pregel = mock_langgraph_imports()

from sentry_sdk.integrations.langgraph import (  # noqa: E402
    LanggraphIntegration,
    _parse_langgraph_messages,
    _wrap_state_graph_compile,
    _wrap_pregel_invoke,
    _wrap_pregel_ainvoke,
)


class MockStateGraph:
    def __init__(self, schema=None):
        self.name = "test_graph"
        self.schema = schema
        self._compiled_graph = None

    def compile(self, *args, **kwargs):
        compiled = MockCompiledGraph(self.name)
        compiled.graph = self
        return compiled


class MockCompiledGraph:
    def __init__(self, name="test_graph"):
        self.name = name
        self._graph = None

    def get_graph(self):
        return MockGraphRepresentation()

    def invoke(self, state, config=None):
        return {"messages": [MockMessage("Response from graph")]}

    async def ainvoke(self, state, config=None):
        return {"messages": [MockMessage("Async response from graph")]}


class MockGraphRepresentation:
    def __init__(self):
        self.nodes = {"tools": MockToolsNode()}


class MockToolsNode:
    def __init__(self):
        self.data = MockToolsData()


class MockToolsData:
    def __init__(self):
        self.tools_by_name = {
            "search_tool": MockTool("search_tool"),
            "calculator": MockTool("calculator"),
        }


class MockTool:
    def __init__(self, name):
        self.name = name


class MockMessage:
    def __init__(
        self,
        content,
        name=None,
        tool_calls=None,
        function_call=None,
        role=None,
        type=None,
        response_metadata=None,
    ):
        self.content = content
        self.name = name
        self.tool_calls = tool_calls
        self.function_call = function_call
        self.role = role
        # The integration uses getattr(message, "type", None) for the role in _normalize_langgraph_message
        # Set default type based on name if type not explicitly provided
        if type is None and name in ["assistant", "ai", "user", "system", "function"]:
            self.type = name
        else:
            self.type = type
        self.response_metadata = response_metadata


class MockPregelInstance:
    def __init__(self, name="test_pregel"):
        self.name = name
        self.graph_name = name

    def invoke(self, state, config=None):
        return {"messages": [MockMessage("Pregel response")]}

    async def ainvoke(self, state, config=None):
        return {"messages": [MockMessage("Async Pregel response")]}


def test_langgraph_integration_init():
    """Test LanggraphIntegration initialization with different parameters."""
    integration = LanggraphIntegration()
    assert integration.include_prompts is True
    assert integration.identifier == "langgraph"
    assert integration.origin == "auto.ai.langgraph"

    integration = LanggraphIntegration(include_prompts=False)
    assert integration.include_prompts is False
    assert integration.identifier == "langgraph"
    assert integration.origin == "auto.ai.langgraph"


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_state_graph_compile(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test StateGraph.compile() wrapper creates proper create_agent span."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()
    graph = MockStateGraph()

    def original_compile(self, *args, **kwargs):
        return MockCompiledGraph(self.name)

    with patch("sentry_sdk.integrations.langgraph.StateGraph"):
        with start_transaction():
            wrapped_compile = _wrap_state_graph_compile(original_compile)
            compiled_graph = wrapped_compile(
                graph, model="test-model", checkpointer=None
            )

    assert compiled_graph is not None
    assert compiled_graph.name == "test_graph"

    tx = events[0]
    assert tx["type"] == "transaction"

    agent_spans = [span for span in tx["spans"] if span["op"] == OP.GEN_AI_CREATE_AGENT]
    assert len(agent_spans) == 1

    agent_span = agent_spans[0]
    assert agent_span["description"] == "create_agent test_graph"
    assert agent_span["origin"] == "auto.ai.langgraph"
    assert agent_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "create_agent"
    assert agent_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "test_graph"
    assert agent_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "test-model"
    assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS in agent_span["data"]

    tools_data = agent_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
    assert tools_data == ["search_tool", "calculator"]
    assert len(tools_data) == 2
    assert "search_tool" in tools_data
    assert "calculator" in tools_data


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_pregel_invoke(sentry_init, capture_events, send_default_pii, include_prompts):
    """Test Pregel.invoke() wrapper creates proper invoke_agent span."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    def original_invoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
            )
        ]
        return {"messages": new_messages}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    assert invoke_span["description"] == "invoke_agent test_graph"
    assert invoke_span["origin"] == "auto.ai.langgraph"
    assert invoke_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert invoke_span["data"][SPANDATA.GEN_AI_PIPELINE_NAME] == "test_graph"
    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "test_graph"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in invoke_span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in invoke_span["data"]

        request_messages = invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]

        if isinstance(request_messages, str):
            import json

            request_messages = json.loads(request_messages)
        assert len(request_messages) == 2
        assert request_messages[0]["content"] == "Hello, can you help me?"
        assert request_messages[1]["content"] == "Of course! How can I assist you?"

        response_text = invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert response_text == expected_assistant_response

        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in invoke_span["data"]
        tool_calls_data = invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS]
        if isinstance(tool_calls_data, str):
            import json

            tool_calls_data = json.loads(tool_calls_data)

        assert len(tool_calls_data) == 1
        assert tool_calls_data[0]["id"] == "call_test_123"
        assert tool_calls_data[0]["function"]["name"] == "search_tool"
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in invoke_span.get("data", {})


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_pregel_ainvoke(sentry_init, capture_events, send_default_pii, include_prompts):
    """Test Pregel.ainvoke() async wrapper creates proper invoke_agent span."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()
    test_state = {"messages": [MockMessage("What's the weather like?", name="user")]}
    pregel = MockPregelInstance("async_graph")

    expected_assistant_response = "It's sunny and 72°F today!"
    expected_tool_calls = [
        {
            "id": "call_weather_456",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"location": "current"}'},
        }
    ]

    async def original_ainvoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
            )
        ]
        return {"messages": new_messages}

    async def run_test():
        with start_transaction():
            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            result = await wrapped_ainvoke(pregel, test_state)
            return result

    result = asyncio.run(run_test())
    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    assert invoke_span["description"] == "invoke_agent async_graph"
    assert invoke_span["origin"] == "auto.ai.langgraph"
    assert invoke_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert invoke_span["data"][SPANDATA.GEN_AI_PIPELINE_NAME] == "async_graph"
    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "async_graph"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in invoke_span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in invoke_span["data"]

        response_text = invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert response_text == expected_assistant_response

        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in invoke_span["data"]
        tool_calls_data = invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS]
        if isinstance(tool_calls_data, str):
            import json

            tool_calls_data = json.loads(tool_calls_data)

        assert len(tool_calls_data) == 1
        assert tool_calls_data[0]["id"] == "call_weather_456"
        assert tool_calls_data[0]["function"]["name"] == "get_weather"
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in invoke_span.get("data", {})


def test_pregel_invoke_error(sentry_init, capture_events):
    """Test error handling during graph execution."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    test_state = {"messages": [MockMessage("This will fail")]}
    pregel = MockPregelInstance("error_graph")

    def original_invoke(self, *args, **kwargs):
        raise Exception("Graph execution failed")

    with start_transaction(), pytest.raises(Exception, match="Graph execution failed"):
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        wrapped_invoke(pregel, test_state)

    tx = events[0]
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    assert invoke_span.get("status") == "internal_error"
    assert invoke_span.get("tags", {}).get("status") == "internal_error"


def test_pregel_ainvoke_error(sentry_init, capture_events):
    """Test error handling during async graph execution."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    test_state = {"messages": [MockMessage("This will fail async")]}
    pregel = MockPregelInstance("async_error_graph")

    async def original_ainvoke(self, *args, **kwargs):
        raise Exception("Async graph execution failed")

    async def run_error_test():
        with start_transaction(), pytest.raises(
            Exception, match="Async graph execution failed"
        ):
            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            await wrapped_ainvoke(pregel, test_state)

    asyncio.run(run_error_test())

    tx = events[0]
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    assert invoke_span.get("status") == "internal_error"
    assert invoke_span.get("tags", {}).get("status") == "internal_error"


def test_span_origin(sentry_init, capture_events):
    """Test that span origins are correctly set."""
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    graph = MockStateGraph()

    def original_compile(self, *args, **kwargs):
        return MockCompiledGraph(self.name)

    with start_transaction():
        from sentry_sdk.integrations.langgraph import _wrap_state_graph_compile

        wrapped_compile = _wrap_state_graph_compile(original_compile)
        wrapped_compile(graph)

    tx = events[0]
    assert tx["contexts"]["trace"]["origin"] == "manual"

    for span in tx["spans"]:
        assert span["origin"] == "auto.ai.langgraph"


@pytest.mark.parametrize("graph_name", ["my_graph", None, ""])
def test_pregel_invoke_with_different_graph_names(
    sentry_init, capture_events, graph_name
):
    """Test Pregel.invoke() with different graph name scenarios."""
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    pregel = MockPregelInstance(graph_name) if graph_name else MockPregelInstance()
    if not graph_name:
        delattr(pregel, "name")
        delattr(pregel, "graph_name")

    def original_invoke(self, *args, **kwargs):
        return {"result": "test"}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        wrapped_invoke(pregel, {"messages": []})

    tx = events[0]
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]

    if graph_name and graph_name.strip():
        assert invoke_span["description"] == "invoke_agent my_graph"
        assert invoke_span["data"][SPANDATA.GEN_AI_PIPELINE_NAME] == graph_name
        assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == graph_name
    else:
        assert invoke_span["description"] == "invoke_agent"
        assert SPANDATA.GEN_AI_PIPELINE_NAME not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_AGENT_NAME not in invoke_span.get("data", {})


def test_pregel_invoke_span_includes_usage_data(sentry_init, capture_events):
    """
    Test that invoke_agent spans include aggregated usage data from context_wrapper.
    This verifies the new functionality added to track token usage in invoke_agent spans.
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    def original_invoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 30,
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                    },
                    "model_name": "gpt-4.1-2025-04-14",
                },
            )
        ]
        return {"messages": new_messages}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span has usage data
    assert invoke_agent_span["description"] == "invoke_agent test_graph"
    assert "gen_ai.usage.input_tokens" in invoke_agent_span["data"]
    assert "gen_ai.usage.output_tokens" in invoke_agent_span["data"]
    assert "gen_ai.usage.total_tokens" in invoke_agent_span["data"]

    # The usage should match the mock_usage values (aggregated across all calls)
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens"] == 10
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens"] == 20
    assert invoke_agent_span["data"]["gen_ai.usage.total_tokens"] == 30


def test_pregel_ainvoke_span_includes_usage_data(sentry_init, capture_events):
    """
    Test that invoke_agent spans include aggregated usage data from context_wrapper.
    This verifies the new functionality added to track token usage in invoke_agent spans.
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    async def original_ainvoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 30,
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                    },
                    "model_name": "gpt-4.1-2025-04-14",
                },
            )
        ]
        return {"messages": new_messages}

    async def run_test():
        with start_transaction():
            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            result = await wrapped_ainvoke(pregel, test_state)
            return result

    result = asyncio.run(run_test())
    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span has usage data
    assert invoke_agent_span["description"] == "invoke_agent test_graph"
    assert "gen_ai.usage.input_tokens" in invoke_agent_span["data"]
    assert "gen_ai.usage.output_tokens" in invoke_agent_span["data"]
    assert "gen_ai.usage.total_tokens" in invoke_agent_span["data"]

    # The usage should match the mock_usage values (aggregated across all calls)
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens"] == 10
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens"] == 20
    assert invoke_agent_span["data"]["gen_ai.usage.total_tokens"] == 30


def test_pregel_invoke_multiple_llm_calls_aggregate_usage(sentry_init, capture_events):
    """
    Test that invoke_agent spans show aggregated usage across multiple LLM calls
    (e.g., when tools are used and multiple API calls are made).
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    def original_invoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 15,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                    },
                },
            ),
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 35,
                        "prompt_tokens": 20,
                        "completion_tokens": 15,
                    },
                },
            ),
        ]
        return {"messages": new_messages}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1
    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span has aggregated usage from both API calls
    # Total: 10 + 20 = 30 input tokens, 5 + 15 = 20 output tokens, 15 + 35 = 50 total
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens"] == 30
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens"] == 20
    assert invoke_agent_span["data"]["gen_ai.usage.total_tokens"] == 50


def test_pregel_ainvoke_multiple_llm_calls_aggregate_usage(sentry_init, capture_events):
    """
    Test that invoke_agent spans show aggregated usage across multiple LLM calls
    (e.g., when tools are used and multiple API calls are made).
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    async def original_ainvoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 15,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                    },
                },
            ),
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 35,
                        "prompt_tokens": 20,
                        "completion_tokens": 15,
                    },
                },
            ),
        ]
        return {"messages": new_messages}

    async def run_test():
        with start_transaction():
            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            result = await wrapped_ainvoke(pregel, test_state)
            return result

    result = asyncio.run(run_test())
    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1
    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span has aggregated usage from both API calls
    # Total: 10 + 20 = 30 input tokens, 5 + 15 = 20 output tokens, 15 + 35 = 50 total
    assert invoke_agent_span["data"]["gen_ai.usage.input_tokens"] == 30
    assert invoke_agent_span["data"]["gen_ai.usage.output_tokens"] == 20
    assert invoke_agent_span["data"]["gen_ai.usage.total_tokens"] == 50


def test_pregel_invoke_span_includes_response_model(sentry_init, capture_events):
    """
    Test that invoke_agent spans include the response model.
    When an agent makes multiple LLM calls, it should report the last model used.
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    def original_invoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 30,
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                    },
                    "model_name": "gpt-4.1-2025-04-14",
                },
            )
        ]
        return {"messages": new_messages}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span has response model
    assert invoke_agent_span["description"] == "invoke_agent test_graph"
    assert "gen_ai.response.model" in invoke_agent_span["data"]
    assert invoke_agent_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"


def test_pregel_ainvoke_span_includes_response_model(sentry_init, capture_events):
    """
    Test that invoke_agent spans include the response model.
    When an agent makes multiple LLM calls, it should report the last model used.
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    async def original_ainvoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 30,
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                    },
                    "model_name": "gpt-4.1-2025-04-14",
                },
            )
        ]
        return {"messages": new_messages}

    async def run_test():
        with start_transaction():
            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            result = await wrapped_ainvoke(pregel, test_state)
            return result

    result = asyncio.run(run_test())
    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span has response model
    assert invoke_agent_span["description"] == "invoke_agent test_graph"
    assert "gen_ai.response.model" in invoke_agent_span["data"]
    assert invoke_agent_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"


def test_pregel_invoke_span_uses_last_response_model(sentry_init, capture_events):
    """
    Test that when an agent makes multiple LLM calls (e.g., with tools),
    the invoke_agent span reports the last response model used.
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    def original_invoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 15,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                    },
                    "model_name": "gpt-4-0613",
                },
            ),
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 35,
                        "prompt_tokens": 20,
                        "completion_tokens": 15,
                    },
                    "model_name": "gpt-4.1-2025-04-14",
                },
            ),
        ]
        return {"messages": new_messages}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span uses the LAST response model
    assert "gen_ai.response.model" in invoke_agent_span["data"]
    assert invoke_agent_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"


def test_pregel_ainvoke_span_uses_last_response_model(sentry_init, capture_events):
    """
    Test that when an agent makes multiple LLM calls (e.g., with tools),
    the invoke_agent span reports the last response model used.
    """
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    expected_assistant_response = "I'll help you with that task!"
    expected_tool_calls = [
        {
            "id": "call_test_123",
            "type": "function",
            "function": {"name": "search_tool", "arguments": '{"query": "help"}'},
        }
    ]

    async def original_ainvoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 15,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                    },
                    "model_name": "gpt-4-0613",
                },
            ),
            MockMessage(
                content=expected_assistant_response,
                name="assistant",
                tool_calls=expected_tool_calls,
                response_metadata={
                    "token_usage": {
                        "total_tokens": 35,
                        "prompt_tokens": 20,
                        "completion_tokens": 15,
                    },
                    "model_name": "gpt-4.1-2025-04-14",
                },
            ),
        ]
        return {"messages": new_messages}

    async def run_test():
        with start_transaction():
            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            result = await wrapped_ainvoke(pregel, test_state)
            return result

    result = asyncio.run(run_test())
    assert result is not None

    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_agent_span = invoke_spans[0]

    # Verify invoke_agent span uses the LAST response model
    assert "gen_ai.response.model" in invoke_agent_span["data"]
    assert invoke_agent_span["data"]["gen_ai.response.model"] == "gpt-4.1-2025-04-14"


def test_complex_message_parsing():
    """Test message parsing with complex message structures."""
    messages = [
        MockMessage(content="User query", name="user"),
        MockMessage(
            content="Assistant response with tools",
            name="assistant",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "calculate", "arguments": '{"x": 5}'},
                },
            ],
        ),
        MockMessage(
            content="Function call response",
            name="function",
            function_call={"name": "search", "arguments": '{"query": "test"}'},
        ),
    ]

    state = {"messages": messages}
    result = _parse_langgraph_messages(state)

    assert result is not None
    assert len(result) == 3

    assert result[0]["content"] == "User query"
    assert result[0]["name"] == "user"
    assert "tool_calls" not in result[0]
    assert "function_call" not in result[0]

    assert result[1]["content"] == "Assistant response with tools"
    assert result[1]["name"] == "assistant"
    assert len(result[1]["tool_calls"]) == 2

    assert result[2]["content"] == "Function call response"
    assert result[2]["name"] == "function"
    assert result[2]["function_call"]["name"] == "search"


def test_extraction_functions_complex_scenario(sentry_init, capture_events):
    """Test extraction functions with complex scenarios including multiple messages and edge cases."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    pregel = MockPregelInstance("complex_graph")
    test_state = {"messages": [MockMessage("Complex request", name="user")]}

    def original_invoke(self, *args, **kwargs):
        input_messages = args[0].get("messages", [])
        new_messages = input_messages + [
            MockMessage(
                content="I'll help with multiple tasks",
                name="assistant",
                tool_calls=[
                    {
                        "id": "call_multi_1",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"query": "complex"}',
                        },
                    },
                    {
                        "id": "call_multi_2",
                        "type": "function",
                        "function": {
                            "name": "calculate",
                            "arguments": '{"expr": "2+2"}',
                        },
                    },
                ],
            ),
            MockMessage("", name="assistant"),
            MockMessage("Final response", name="ai", type="ai"),
        ]
        return {"messages": new_messages}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    assert result is not None

    tx = events[0]
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    assert SPANDATA.GEN_AI_RESPONSE_TEXT in invoke_span["data"]
    response_text = invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    assert response_text == "Final response"

    assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in invoke_span["data"]
    import json

    tool_calls_data = invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS]
    if isinstance(tool_calls_data, str):
        tool_calls_data = json.loads(tool_calls_data)

    assert len(tool_calls_data) == 2
    assert tool_calls_data[0]["id"] == "call_multi_1"
    assert tool_calls_data[0]["function"]["name"] == "search"
    assert tool_calls_data[1]["id"] == "call_multi_2"
    assert tool_calls_data[1]["function"]["name"] == "calculate"


def test_langgraph_message_role_mapping(sentry_init, capture_events):
    """Test that Langgraph integration properly maps message roles like 'ai' to 'assistant'"""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Mock a langgraph message with mixed roles
    class MockMessage:
        def __init__(self, content, message_type="human"):
            self.content = content
            self.type = message_type

    # Create mock state with messages having different roles
    state_data = {
        "messages": [
            MockMessage("System prompt", "system"),
            MockMessage("Hello", "human"),
            MockMessage("Hi there!", "ai"),  # Should be mapped to "assistant"
            MockMessage("How can I help?", "assistant"),  # Should stay "assistant"
        ]
    }

    compiled_graph = MockCompiledGraph("test_graph")
    pregel = MockPregelInstance(compiled_graph)

    with start_transaction(name="langgraph tx"):
        # Use the wrapped invoke function directly
        from sentry_sdk.integrations.langgraph import _wrap_pregel_invoke

        wrapped_invoke = _wrap_pregel_invoke(
            lambda self, state_data: {"result": "success"}
        )
        wrapped_invoke(pregel, state_data)

    (event,) = events
    span = event["spans"][0]

    # Verify that the span was created correctly
    assert span["op"] == "gen_ai.invoke_agent"

    # If messages were captured, verify role mapping
    if SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]:
        import json

        stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

        # Find messages with specific content to verify role mapping
        ai_message = next(
            (msg for msg in stored_messages if msg.get("content") == "Hi there!"), None
        )
        assistant_message = next(
            (msg for msg in stored_messages if msg.get("content") == "How can I help?"),
            None,
        )

        if ai_message:
            # "ai" should have been mapped to "assistant"
            assert ai_message["role"] == "assistant"

        if assistant_message:
            # "assistant" should stay "assistant"
            assert assistant_message["role"] == "assistant"

        # Verify no "ai" roles remain
        roles = [msg["role"] for msg in stored_messages if "role" in msg]
        assert "ai" not in roles


def test_langgraph_message_truncation(sentry_init, capture_events):
    """Test that large messages are truncated properly in Langgraph integration."""
    import json

    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )
    test_state = {
        "messages": [
            MockMessage("small message 1", name="user"),
            MockMessage(large_content, name="assistant"),
            MockMessage(large_content, name="user"),
            MockMessage("small message 4", name="assistant"),
            MockMessage("small message 5", name="user"),
        ]
    }

    pregel = MockPregelInstance("test_graph")

    def original_invoke(self, *args, **kwargs):
        return {"messages": args[0].get("messages", [])}

    with start_transaction():
        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    assert result is not None
    assert len(events) > 0
    tx = events[0]
    assert tx["type"] == "transaction"

    invoke_spans = [
        span for span in tx.get("spans", []) if span.get("op") == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) > 0

    invoke_span = invoke_spans[0]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in invoke_span["data"]

    messages_data = invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) == 2
    assert "small message 4" in str(parsed_messages[0])
    assert "small message 5" in str(parsed_messages[1])
    assert tx["_meta"]["spans"]["0"]["data"]["gen_ai.request.messages"][""]["len"] == 5

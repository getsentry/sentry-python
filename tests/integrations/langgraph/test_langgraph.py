import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA, OP


# Mock langgraph modules before importing the integration
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


# Mock the imports
mock_state_graph, mock_pregel = mock_langgraph_imports()

# Now we can safely import the integration
from sentry_sdk.integrations.langgraph import (
    LanggraphIntegration,
    _parse_langgraph_messages,
    _get_graph_name,
    _wrap_state_graph_compile,
    _wrap_pregel_invoke,
    _wrap_pregel_ainvoke,
)


# Mock LangGraph dependencies
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
        # Simulate graph execution
        return {"messages": [MockMessage("Response from graph")]}

    async def ainvoke(self, state, config=None):
        # Simulate async graph execution
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
    def __init__(self, content, name=None, tool_calls=None, function_call=None):
        self.content = content
        self.name = name
        self.tool_calls = tool_calls
        self.function_call = function_call


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
    # Test default initialization
    integration = LanggraphIntegration()
    assert integration.include_prompts is True
    assert integration.identifier == "langgraph"
    assert integration.origin == "auto.ai.langgraph"

    # Test with include_prompts=False
    integration = LanggraphIntegration(include_prompts=False)
    assert integration.include_prompts is False


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

    # Create a mock StateGraph
    graph = MockStateGraph()

    # Mock the original compile method
    def original_compile(self, *args, **kwargs):
        return MockCompiledGraph(self.name)

    with patch("sentry_sdk.integrations.langgraph.StateGraph"):
        # Test the compile wrapper
        with start_transaction():

            wrapped_compile = _wrap_state_graph_compile(original_compile)
            compiled_graph = wrapped_compile(
                graph, model="test-model", checkpointer=None
            )

    # Verify the compiled graph is returned
    assert compiled_graph is not None
    assert compiled_graph.name == "test_graph"

    # Check events
    tx = events[0]
    assert tx["type"] == "transaction"

    # Find the create_agent span
    agent_spans = [span for span in tx["spans"] if span["op"] == OP.GEN_AI_CREATE_AGENT]
    assert len(agent_spans) == 1

    agent_span = agent_spans[0]
    assert agent_span["description"] == "create_agent"
    assert agent_span["origin"] == "auto.ai.langgraph"
    assert agent_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "create_agent"
    assert agent_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "test_graph"
    assert agent_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "test-model"
    assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS in agent_span["data"]


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

    # Create test state with messages
    test_state = {
        "messages": [
            MockMessage("Hello, can you help me?", name="user"),
            MockMessage("Of course! How can I assist you?", name="assistant"),
        ]
    }

    # Create mock Pregel instance
    pregel = MockPregelInstance("test_graph")

    # Mock the original invoke method
    def original_invoke(self, *args, **kwargs):
        return {"messages": [MockMessage("Response")]}

    with start_transaction():

        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        result = wrapped_invoke(pregel, test_state)

    # Verify result
    assert result is not None

    # Check events
    tx = events[0]
    assert tx["type"] == "transaction"

    # Find the invoke_agent span
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    assert "invoke_agent invoke test_graph" in invoke_span["description"]
    assert invoke_span["origin"] == "auto.ai.langgraph"
    assert invoke_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert invoke_span["data"][SPANDATA.GEN_AI_PIPELINE_NAME] == "test_graph"
    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "test_graph"
    assert invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False

    # Check PII handling
    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in invoke_span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in invoke_span["data"]
        # Verify message content is captured
        request_messages = invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        # The messages might be serialized as a string, so parse if needed
        if isinstance(request_messages, str):
            import json

            request_messages = json.loads(request_messages)
        assert len(request_messages) == 2
        assert request_messages[0]["content"] == "Hello, can you help me?"
        assert request_messages[1]["content"] == "Of course! How can I assist you?"
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in invoke_span.get("data", {})


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

    # Create test state with messages
    test_state = {"messages": [MockMessage("What's the weather like?", name="user")]}

    # Create mock Pregel instance
    pregel = MockPregelInstance("async_graph")

    # Mock the original ainvoke method
    async def original_ainvoke(self, *args, **kwargs):
        return {"messages": [MockMessage("It's sunny today!")]}

    async def run_test():
        with start_transaction():

            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            result = await wrapped_ainvoke(pregel, test_state)
            return result

    # Run the async test
    result = asyncio.run(run_test())
    assert result is not None

    # Check events
    tx = events[0]
    assert tx["type"] == "transaction"

    # Find the invoke_agent span
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    assert invoke_span["description"] == "invoke_agent"
    assert invoke_span["origin"] == "auto.ai.langgraph"
    assert invoke_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert invoke_span["data"][SPANDATA.GEN_AI_PIPELINE_NAME] == "async_graph"
    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "async_graph"

    # Check PII handling
    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in invoke_span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT in invoke_span["data"]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in invoke_span.get("data", {})


def test_pregel_invoke_error(sentry_init, capture_events):
    """Test error handling during graph execution."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Create test state
    test_state = {"messages": [MockMessage("This will fail")]}

    # Create mock Pregel instance
    pregel = MockPregelInstance("error_graph")

    # Mock the original invoke method to raise an exception
    def original_invoke(self, *args, **kwargs):
        raise Exception("Graph execution failed")

    with start_transaction(), pytest.raises(Exception, match="Graph execution failed"):

        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        wrapped_invoke(pregel, test_state)

    # Check that error was captured
    tx = events[0]
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    # Check if error status is recorded (status is stored in tags)
    assert invoke_span.get("tags", {}).get("status") == "internal_error"


def test_pregel_ainvoke_error(sentry_init, capture_events):
    """Test error handling during async graph execution."""
    sentry_init(
        integrations=[LanggraphIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Create test state
    test_state = {"messages": [MockMessage("This will fail async")]}

    # Create mock Pregel instance
    pregel = MockPregelInstance("async_error_graph")

    # Mock the original ainvoke method to raise an exception
    async def original_ainvoke(self, *args, **kwargs):
        raise Exception("Async graph execution failed")

    async def run_error_test():
        with start_transaction(), pytest.raises(
            Exception, match="Async graph execution failed"
        ):

            wrapped_ainvoke = _wrap_pregel_ainvoke(original_ainvoke)
            await wrapped_ainvoke(pregel, test_state)

    # Run the async error test
    asyncio.run(run_error_test())

    # Check that error was captured
    tx = events[0]
    invoke_spans = [
        span for span in tx["spans"] if span["op"] == OP.GEN_AI_INVOKE_AGENT
    ]
    assert len(invoke_spans) == 1

    invoke_span = invoke_spans[0]
    # Check if error status is recorded (status is stored in tags)
    assert invoke_span.get("tags", {}).get("status") == "internal_error"


def test_parse_langgraph_messages_dict_state():
    """Test _parse_langgraph_messages with dict state containing messages."""
    messages = [
        MockMessage("Hello", name="user"),
        MockMessage("Hi there", name="assistant", tool_calls=[{"name": "search"}]),
        MockMessage("Search result", function_call={"name": "search", "args": {}}),
    ]

    state = {"messages": messages}

    result = _parse_langgraph_messages(state)

    assert result is not None
    assert len(result) == 3

    assert result[0]["content"] == "Hello"
    assert result[0]["name"] == "user"

    assert result[1]["content"] == "Hi there"
    assert result[1]["name"] == "assistant"
    assert result[1]["tool_calls"] == [{"name": "search"}]

    assert result[2]["content"] == "Search result"
    assert result[2]["function_call"] == {"name": "search", "args": {}}


def test_parse_langgraph_messages_object_state():
    """Test _parse_langgraph_messages with object state having messages attribute."""

    class StateWithMessages:
        def __init__(self):
            self.messages = [
                MockMessage("Test message", name="user"),
                MockMessage("Response", name="assistant"),
            ]

    state = StateWithMessages()

    result = _parse_langgraph_messages(state)

    assert result is not None
    assert len(result) == 2
    assert result[0]["content"] == "Test message"
    assert result[1]["content"] == "Response"


def test_parse_langgraph_messages_callable_get():
    """Test _parse_langgraph_messages with state that has callable get method."""

    class StateWithGet:
        def __init__(self):
            self._data = {"messages": [MockMessage("From get method", name="system")]}

        def get(self, key):
            return self._data.get(key)

    state = StateWithGet()

    result = _parse_langgraph_messages(state)

    assert result is not None
    assert len(result) == 1
    assert result[0]["content"] == "From get method"
    assert result[0]["name"] == "system"


def test_parse_langgraph_messages_invalid_cases():
    """Test _parse_langgraph_messages with various invalid inputs."""
    # None state
    assert _parse_langgraph_messages(None) is None

    # Empty state
    assert _parse_langgraph_messages({}) is None

    # State without messages
    assert _parse_langgraph_messages({"other": "data"}) is None

    # State with non-list messages
    assert _parse_langgraph_messages({"messages": "not a list"}) is None

    # State with empty messages list
    assert _parse_langgraph_messages({"messages": []}) is None

    # Messages that don't have content attribute
    class BadMessage:
        def __init__(self):
            self.text = "I don't have content"

    assert _parse_langgraph_messages({"messages": [BadMessage()]}) is None


def test_parse_langgraph_messages_exception_handling():
    """Test _parse_langgraph_messages handles exceptions gracefully."""

    class ProblematicState:
        def get(self, key):
            raise Exception("Something went wrong")

    state = ProblematicState()

    result = _parse_langgraph_messages(state)
    assert result is None


def test_get_graph_name():
    """Test _get_graph_name function with different graph objects."""

    # Test with name attribute
    class GraphWithName:
        name = "graph_with_name"

    assert _get_graph_name(GraphWithName()) == "graph_with_name"

    # Test with graph_name attribute
    class GraphWithGraphName:
        graph_name = "graph_with_graph_name"

    assert _get_graph_name(GraphWithGraphName()) == "graph_with_graph_name"

    # Test with __name__ attribute
    class GraphWithDunderName:
        __name__ = "graph_with_dunder_name"

    assert _get_graph_name(GraphWithDunderName()) == "graph_with_dunder_name"

    # Test with _name attribute
    class GraphWithPrivateName:
        _name = "graph_with_private_name"

    assert _get_graph_name(GraphWithPrivateName()) == "graph_with_private_name"

    # Test with no name attributes
    class GraphWithoutName:
        pass

    assert _get_graph_name(GraphWithoutName()) is None

    # Test with empty/None name
    class GraphWithEmptyName:
        name = None

    assert _get_graph_name(GraphWithEmptyName()) is None

    class GraphWithEmptyStringName:
        name = ""

    assert _get_graph_name(GraphWithEmptyStringName()) is None


def test_span_origin(sentry_init, capture_events):
    """Test that span origins are correctly set."""
    sentry_init(
        integrations=[LanggraphIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Test state graph compile
    graph = MockStateGraph()

    def original_compile(self, *args, **kwargs):
        return MockCompiledGraph(self.name)

    with start_transaction():
        from sentry_sdk.integrations.langgraph import _wrap_state_graph_compile

        wrapped_compile = _wrap_state_graph_compile(original_compile)
        wrapped_compile(graph)

    tx = events[0]
    assert tx["contexts"]["trace"]["origin"] == "manual"

    # Check all spans have the correct origin
    for span in tx["spans"]:
        assert span["origin"] == "auto.ai.langgraph"


def test_no_spans_without_integration(sentry_init, capture_events):
    """Test that no spans are created when integration is not enabled."""
    # Initialize without LanggraphIntegration
    sentry_init(
        integrations=[],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    graph = MockStateGraph()
    pregel = MockPregelInstance()

    with start_transaction():
        # These should not create spans

        def original_compile(self, *args, **kwargs):
            return MockCompiledGraph(self.name)

        wrapped_compile = _wrap_state_graph_compile(original_compile)
        wrapped_compile(graph)

        def original_invoke(self, *args, **kwargs):
            return {"result": "test"}

        wrapped_invoke = _wrap_pregel_invoke(original_invoke)
        wrapped_invoke(pregel, {"messages": []})

    tx = events[0]

    # Should only have the manual transaction, no AI spans
    ai_spans = [
        span
        for span in tx["spans"]
        if span["op"] in [OP.GEN_AI_CREATE_AGENT, OP.GEN_AI_INVOKE_AGENT]
    ]
    assert len(ai_spans) == 0


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

    # Create mock Pregel instance with specific name
    pregel = MockPregelInstance(graph_name) if graph_name else MockPregelInstance()
    if not graph_name:
        # Remove name attributes to test None case
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
        assert f"invoke_agent invoke {graph_name}" in invoke_span["description"]
        assert invoke_span["data"][SPANDATA.GEN_AI_PIPELINE_NAME] == graph_name
        assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == graph_name
    else:
        assert invoke_span["description"] == "invoke_agent"
        assert SPANDATA.GEN_AI_PIPELINE_NAME not in invoke_span.get("data", {})
        assert SPANDATA.GEN_AI_AGENT_NAME not in invoke_span.get("data", {})


def test_complex_message_parsing():
    """Test message parsing with complex message structures."""
    # Create messages with various attributes
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

    # Check first message
    assert result[0]["content"] == "User query"
    assert result[0]["name"] == "user"
    assert "tool_calls" not in result[0]
    assert "function_call" not in result[0]

    # Check second message with tool calls
    assert result[1]["content"] == "Assistant response with tools"
    assert result[1]["name"] == "assistant"
    assert len(result[1]["tool_calls"]) == 2

    # Check third message with function call
    assert result[2]["content"] == "Function call response"
    assert result[2]["name"] == "function"
    assert result[2]["function_call"]["name"] == "search"

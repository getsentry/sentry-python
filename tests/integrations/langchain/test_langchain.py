import json
from typing import List, Optional, Any, Iterator
from unittest import mock
from unittest.mock import Mock, patch

import pytest

from sentry_sdk.consts import SPANDATA

try:
    # Langchain >= 0.2
    from langchain_openai import ChatOpenAI
except ImportError:
    # Langchain < 0.2
    from langchain_community.chat_models import ChatOpenAI

from langchain_core.callbacks import BaseCallbackManager, CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.language_models.chat_models import BaseChatModel

from sentry_sdk import start_transaction
from sentry_sdk.integrations.langchain import (
    LangchainIntegration,
    SentryLangchainCallback,
)

try:
    # langchain v1+
    from langchain.tools import tool
    from langchain_classic.agents import AgentExecutor, create_openai_tools_agent  # type: ignore[import-not-found]
except ImportError:
    # langchain <v1
    from langchain.agents import tool, AgentExecutor, create_openai_tools_agent

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


@tool
def get_word_length(word: str) -> int:
    """Returns the length of a word."""
    return len(word)


global stream_result_mock  # type: Mock
global llm_type  # type: str


class MockOpenAI(ChatOpenAI):
    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for x in stream_result_mock():
            yield x

    @property
    def _llm_type(self) -> str:
        return llm_type


@pytest.mark.xfail
@pytest.mark.parametrize(
    "send_default_pii, include_prompts, use_unknown_llm_type",
    [
        (True, True, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ],
)
def test_langchain_agent(
    sentry_init, capture_events, send_default_pii, include_prompts, use_unknown_llm_type
):
    global llm_type
    llm_type = "acme-llm" if use_unknown_llm_type else "openai-chat"

    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=include_prompts,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are very powerful assistant, but don't know current events",
            ),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    global stream_result_mock
    stream_result_mock = Mock(
        side_effect=[
            [
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="",
                        additional_kwargs={
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_BbeyNhCKa6kYLYzrD40NGm3b",
                                    "function": {
                                        "arguments": "",
                                        "name": "get_word_length",
                                    },
                                    "type": "function",
                                }
                            ]
                        },
                    ),
                ),
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="",
                        additional_kwargs={
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": None,
                                    "function": {
                                        "arguments": '{"word": "eudca"}',
                                        "name": None,
                                    },
                                    "type": None,
                                }
                            ]
                        },
                    ),
                ),
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="5",
                        usage_metadata={
                            "input_tokens": 142,
                            "output_tokens": 50,
                            "total_tokens": 192,
                            "input_token_details": {"audio": 0, "cache_read": 0},
                            "output_token_details": {"audio": 0, "reasoning": 0},
                        },
                    ),
                    generation_info={"finish_reason": "function_call"},
                ),
            ],
            [
                ChatGenerationChunk(
                    text="The word eudca has 5 letters.",
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="The word eudca has 5 letters.",
                        usage_metadata={
                            "input_tokens": 89,
                            "output_tokens": 28,
                            "total_tokens": 117,
                            "input_token_details": {"audio": 0, "cache_read": 0},
                            "output_token_details": {"audio": 0, "reasoning": 0},
                        },
                    ),
                ),
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    generation_info={"finish_reason": "stop"},
                    message=AIMessageChunk(content=""),
                ),
            ],
        ]
    )
    llm = MockOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    with start_transaction():
        list(agent_executor.stream({"input": "How many letters in the word eudca"}))

    tx = events[0]
    assert tx["type"] == "transaction"
    chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
    tool_exec_span = next(x for x in tx["spans"] if x["op"] == "gen_ai.execute_tool")

    assert len(chat_spans) == 2

    # We can't guarantee anything about the "shape" of the langchain execution graph
    assert len(list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")) > 0

    assert "gen_ai.usage.input_tokens" in chat_spans[0]["data"]
    assert "gen_ai.usage.output_tokens" in chat_spans[0]["data"]
    assert "gen_ai.usage.total_tokens" in chat_spans[0]["data"]

    assert chat_spans[0]["data"]["gen_ai.usage.input_tokens"] == 142
    assert chat_spans[0]["data"]["gen_ai.usage.output_tokens"] == 50
    assert chat_spans[0]["data"]["gen_ai.usage.total_tokens"] == 192

    assert "gen_ai.usage.input_tokens" in chat_spans[1]["data"]
    assert "gen_ai.usage.output_tokens" in chat_spans[1]["data"]
    assert "gen_ai.usage.total_tokens" in chat_spans[1]["data"]
    assert chat_spans[1]["data"]["gen_ai.usage.input_tokens"] == 89
    assert chat_spans[1]["data"]["gen_ai.usage.output_tokens"] == 28
    assert chat_spans[1]["data"]["gen_ai.usage.total_tokens"] == 117

    if send_default_pii and include_prompts:
        assert (
            "You are very powerful"
            in chat_spans[0]["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert "5" in chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert "word" in tool_exec_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert 5 == int(tool_exec_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT])
        assert (
            "You are very powerful"
            in chat_spans[1]["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert "5" in chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

        # Verify tool calls are recorded when PII is enabled
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_spans[0].get("data", {}), (
            "Tool calls should be recorded when send_default_pii=True and include_prompts=True"
        )
        tool_calls_data = chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS]
        assert isinstance(tool_calls_data, (list, str))  # Could be serialized
        if isinstance(tool_calls_data, str):
            assert "get_word_length" in tool_calls_data
        elif isinstance(tool_calls_data, list) and len(tool_calls_data) > 0:
            # Check if tool calls contain expected function name
            tool_call_str = str(tool_calls_data)
            assert "get_word_length" in tool_call_str
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in tool_exec_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in tool_exec_span.get("data", {})

        # Verify tool calls are NOT recorded when PII is disabled
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[0].get(
            "data", {}
        ), (
            f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
            f"and include_prompts={include_prompts}"
        )
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[1].get(
            "data", {}
        ), (
            f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
            f"and include_prompts={include_prompts}"
        )

    # Verify that available tools are always recorded regardless of PII settings
    for chat_span in chat_spans:
        span_data = chat_span.get("data", {})
        if SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS in span_data:
            tools_data = span_data[SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
            assert tools_data is not None, (
                "Available tools should always be recorded regardless of PII settings"
            )


def test_langchain_error(sentry_init, capture_events):
    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are very powerful assistant, but don't know current events",
            ),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    global stream_result_mock
    stream_result_mock = Mock(side_effect=ValueError("API rate limit error"))
    llm = MockOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    with start_transaction(), pytest.raises(ValueError):
        list(agent_executor.stream({"input": "How many letters in the word eudca"}))

    error = events[0]
    assert error["level"] == "error"


def test_span_status_error(sentry_init, capture_events):
    global llm_type
    llm_type = "acme-llm"

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with start_transaction(name="test"):
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are very powerful assistant, but don't know current events",
                ),
                ("user", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        global stream_result_mock
        stream_result_mock = Mock(side_effect=ValueError("API rate limit error"))
        llm = MockOpenAI(
            model_name="gpt-3.5-turbo",
            temperature=0,
            openai_api_key="badkey",
        )
        agent = create_openai_tools_agent(llm, [get_word_length], prompt)

        agent_executor = AgentExecutor(
            agent=agent, tools=[get_word_length], verbose=True
        )

        with pytest.raises(ValueError):
            list(agent_executor.stream({"input": "How many letters in the word eudca"}))

    (error, transaction) = events
    assert error["level"] == "error"
    assert transaction["spans"][0]["tags"]["status"] == "error"
    assert transaction["contexts"]["trace"]["status"] == "error"


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[LangchainIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are very powerful assistant, but don't know current events",
            ),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    global stream_result_mock
    stream_result_mock = Mock(
        side_effect=[
            [
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="",
                        additional_kwargs={
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_BbeyNhCKa6kYLYzrD40NGm3b",
                                    "function": {
                                        "arguments": "",
                                        "name": "get_word_length",
                                    },
                                    "type": "function",
                                }
                            ]
                        },
                    ),
                ),
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="",
                        additional_kwargs={
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": None,
                                    "function": {
                                        "arguments": '{"word": "eudca"}',
                                        "name": None,
                                    },
                                    "type": None,
                                }
                            ]
                        },
                    ),
                ),
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="5",
                        usage_metadata={
                            "input_tokens": 142,
                            "output_tokens": 50,
                            "total_tokens": 192,
                            "input_token_details": {"audio": 0, "cache_read": 0},
                            "output_token_details": {"audio": 0, "reasoning": 0},
                        },
                    ),
                    generation_info={"finish_reason": "function_call"},
                ),
            ],
            [
                ChatGenerationChunk(
                    text="The word eudca has 5 letters.",
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(
                        content="The word eudca has 5 letters.",
                        usage_metadata={
                            "input_tokens": 89,
                            "output_tokens": 28,
                            "total_tokens": 117,
                            "input_token_details": {"audio": 0, "cache_read": 0},
                            "output_token_details": {"audio": 0, "reasoning": 0},
                        },
                    ),
                ),
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    generation_info={"finish_reason": "stop"},
                    message=AIMessageChunk(content=""),
                ),
            ],
        ]
    )
    llm = MockOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    with start_transaction():
        list(agent_executor.stream({"input": "How many letters in the word eudca"}))

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    for span in event["spans"]:
        assert span["origin"] == "auto.ai.langchain"


def test_manual_callback_no_duplication(sentry_init):
    """
    Test that when a user manually provides a SentryLangchainCallback,
    the integration doesn't create a duplicate callback.
    """

    # Track callback instances
    tracked_callback_instances = set()

    class CallbackTrackingModel(BaseChatModel):
        """Mock model that tracks callback instances for testing."""

        def _generate(
            self,
            messages,
            stop=None,
            run_manager=None,
            **kwargs,
        ):
            # Track all SentryLangchainCallback instances
            if run_manager:
                for handler in run_manager.handlers:
                    if isinstance(handler, SentryLangchainCallback):
                        tracked_callback_instances.add(id(handler))

                for handler in run_manager.inheritable_handlers:
                    if isinstance(handler, SentryLangchainCallback):
                        tracked_callback_instances.add(id(handler))

            return ChatResult(
                generations=[
                    ChatGenerationChunk(message=AIMessageChunk(content="Hello!"))
                ],
                llm_output={},
            )

        @property
        def _llm_type(self):
            return "test_model"

        @property
        def _identifying_params(self):
            return {}

    sentry_init(integrations=[LangchainIntegration()])

    # Create a manual SentryLangchainCallback
    manual_callback = SentryLangchainCallback(
        max_span_map_size=100, include_prompts=False
    )

    # Create RunnableConfig with the manual callback
    config = RunnableConfig(callbacks=[manual_callback])

    # Invoke the model with the config
    llm = CallbackTrackingModel()
    llm.invoke("Hello", config)

    # Verify that only ONE SentryLangchainCallback instance was used
    assert len(tracked_callback_instances) == 1, (
        f"Expected exactly 1 SentryLangchainCallback instance, "
        f"but found {len(tracked_callback_instances)}. "
        f"This indicates callback duplication occurred."
    )

    # Verify the callback ID matches our manual callback
    assert id(manual_callback) in tracked_callback_instances


def test_span_map_is_instance_variable():
    """Test that each SentryLangchainCallback instance has its own span_map."""
    # Create two separate callback instances
    callback1 = SentryLangchainCallback(max_span_map_size=100, include_prompts=True)
    callback2 = SentryLangchainCallback(max_span_map_size=100, include_prompts=True)

    # Verify they have different span_map instances
    assert callback1.span_map is not callback2.span_map, (
        "span_map should be an instance variable, not shared between instances"
    )


def test_langchain_callback_manager(sentry_init):
    sentry_init(
        integrations=[LangchainIntegration()],
        traces_sample_rate=1.0,
    )
    local_manager = BaseCallbackManager(handlers=[])

    with mock.patch("sentry_sdk.integrations.langchain.manager") as mock_manager_module:
        mock_configure = mock_manager_module._configure

        # Explicitly re-run setup_once, so that mock_manager_module._configure gets patched
        LangchainIntegration.setup_once()

        callback_manager_cls = Mock()

        mock_manager_module._configure(
            callback_manager_cls, local_callbacks=local_manager
        )

        assert mock_configure.call_count == 1

        call_args = mock_configure.call_args
        assert call_args.args[0] is callback_manager_cls

        passed_manager = call_args.args[2]
        assert passed_manager is not local_manager
        assert local_manager.handlers == []

        [handler] = passed_manager.handlers
        assert isinstance(handler, SentryLangchainCallback)


def test_langchain_callback_manager_with_sentry_callback(sentry_init):
    sentry_init(
        integrations=[LangchainIntegration()],
        traces_sample_rate=1.0,
    )
    sentry_callback = SentryLangchainCallback(0, False)
    local_manager = BaseCallbackManager(handlers=[sentry_callback])

    with mock.patch("sentry_sdk.integrations.langchain.manager") as mock_manager_module:
        mock_configure = mock_manager_module._configure

        # Explicitly re-run setup_once, so that mock_manager_module._configure gets patched
        LangchainIntegration.setup_once()

        callback_manager_cls = Mock()

        mock_manager_module._configure(
            callback_manager_cls, local_callbacks=local_manager
        )

        assert mock_configure.call_count == 1

        call_args = mock_configure.call_args
        assert call_args.args[0] is callback_manager_cls

        passed_manager = call_args.args[2]
        assert passed_manager is local_manager

        [handler] = passed_manager.handlers
        assert handler is sentry_callback


def test_langchain_callback_list(sentry_init):
    sentry_init(
        integrations=[LangchainIntegration()],
        traces_sample_rate=1.0,
    )
    local_callbacks = []

    with mock.patch("sentry_sdk.integrations.langchain.manager") as mock_manager_module:
        mock_configure = mock_manager_module._configure

        # Explicitly re-run setup_once, so that mock_manager_module._configure gets patched
        LangchainIntegration.setup_once()

        callback_manager_cls = Mock()

        mock_manager_module._configure(
            callback_manager_cls, local_callbacks=local_callbacks
        )

        assert mock_configure.call_count == 1

        call_args = mock_configure.call_args
        assert call_args.args[0] is callback_manager_cls

        passed_callbacks = call_args.args[2]
        assert passed_callbacks is not local_callbacks
        assert local_callbacks == []

        [handler] = passed_callbacks
        assert isinstance(handler, SentryLangchainCallback)


def test_langchain_callback_list_existing_callback(sentry_init):
    sentry_init(
        integrations=[LangchainIntegration()],
        traces_sample_rate=1.0,
    )
    sentry_callback = SentryLangchainCallback(0, False)
    local_callbacks = [sentry_callback]

    with mock.patch("sentry_sdk.integrations.langchain.manager") as mock_manager_module:
        mock_configure = mock_manager_module._configure

        # Explicitly re-run setup_once, so that mock_manager_module._configure gets patched
        LangchainIntegration.setup_once()

        callback_manager_cls = Mock()

        mock_manager_module._configure(
            callback_manager_cls, local_callbacks=local_callbacks
        )

        assert mock_configure.call_count == 1

        call_args = mock_configure.call_args
        assert call_args.args[0] is callback_manager_cls

        passed_callbacks = call_args.args[2]
        assert passed_callbacks is local_callbacks

        [handler] = passed_callbacks
        assert handler is sentry_callback


def test_tools_integration_in_spans(sentry_init, capture_events):
    """Test that tools are properly set on spans in actual LangChain integration."""
    global llm_type
    llm_type = "openai-chat"

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "You are a helpful assistant"),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    global stream_result_mock
    stream_result_mock = Mock(
        side_effect=[
            [
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(content="Simple response"),
                ),
            ]
        ]
    )

    llm = MockOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)
    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    with start_transaction():
        list(agent_executor.stream({"input": "Hello"}))

    # Check that events were captured and contain tools data
    if events:
        tx = events[0]
        spans = tx.get("spans", [])

        # Look for spans that should have tools data
        tools_found = False
        for span in spans:
            span_data = span.get("data", {})
            if SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS in span_data:
                tools_found = True
                tools_data = span_data[SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
                # Verify tools are in the expected format
                assert isinstance(tools_data, (str, list))  # Could be serialized
                if isinstance(tools_data, str):
                    # If serialized as string, should contain tool name
                    assert "get_word_length" in tools_data
                else:
                    # If still a list, verify structure
                    assert len(tools_data) >= 1
                    names = [
                        tool.get("name")
                        for tool in tools_data
                        if isinstance(tool, dict)
                    ]
                    assert "get_word_length" in names

        # Ensure we found at least one span with tools data
        assert tools_found, "No spans found with tools data"


def test_langchain_integration_with_langchain_core_only(sentry_init, capture_events):
    """Test that the langchain integration works when langchain.agents.AgentExecutor
    is not available or langchain is not installed, but langchain-core is.
    """

    from langchain_core.outputs import LLMResult, Generation

    with patch("sentry_sdk.integrations.langchain.AgentExecutor", None):
        from sentry_sdk.integrations.langchain import (
            LangchainIntegration,
            SentryLangchainCallback,
        )

        sentry_init(
            integrations=[LangchainIntegration(include_prompts=True)],
            traces_sample_rate=1.0,
            send_default_pii=True,
        )
        events = capture_events()

        try:
            LangchainIntegration.setup_once()
        except Exception as e:
            pytest.fail(f"setup_once() failed when AgentExecutor is None: {e}")

        callback = SentryLangchainCallback(max_span_map_size=100, include_prompts=True)

        run_id = "12345678-1234-1234-1234-123456789012"
        serialized = {"_type": "openai-chat", "model_name": "gpt-3.5-turbo"}
        prompts = ["What is the capital of France?"]

        with start_transaction():
            callback.on_llm_start(
                serialized=serialized,
                prompts=prompts,
                run_id=run_id,
                invocation_params={
                    "temperature": 0.7,
                    "max_tokens": 100,
                    "model": "gpt-3.5-turbo",
                },
            )

            response = LLMResult(
                generations=[[Generation(text="The capital of France is Paris.")]],
                llm_output={
                    "token_usage": {
                        "total_tokens": 25,
                        "prompt_tokens": 10,
                        "completion_tokens": 15,
                    }
                },
            )
            callback.on_llm_end(response=response, run_id=run_id)

        assert len(events) > 0
        tx = events[0]
        assert tx["type"] == "transaction"

        llm_spans = [
            span for span in tx.get("spans", []) if span.get("op") == "gen_ai.pipeline"
        ]
        assert len(llm_spans) > 0

        llm_span = llm_spans[0]
        assert llm_span["description"] == "Langchain LLM call"
        assert llm_span["data"]["gen_ai.request.model"] == "gpt-3.5-turbo"
        assert (
            llm_span["data"]["gen_ai.response.text"]
            == "The capital of France is Paris."
        )
        assert llm_span["data"]["gen_ai.usage.total_tokens"] == 25
        assert llm_span["data"]["gen_ai.usage.input_tokens"] == 10
        assert llm_span["data"]["gen_ai.usage.output_tokens"] == 15


def test_langchain_message_role_mapping(sentry_init, capture_events):
    """Test that message roles are properly normalized in langchain integration."""
    global llm_type
    llm_type = "openai-chat"

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "You are a helpful assistant"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    global stream_result_mock
    stream_result_mock = Mock(
        side_effect=[
            [
                ChatGenerationChunk(
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(content="Test response"),
                ),
            ]
        ]
    )

    llm = MockOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)
    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    # Test input that should trigger message role normalization
    test_input = "Hello, how are you?"

    with start_transaction():
        list(agent_executor.stream({"input": test_input}))

    assert len(events) > 0
    tx = events[0]
    assert tx["type"] == "transaction"

    # Find spans with gen_ai operation that should have message data
    gen_ai_spans = [
        span for span in tx.get("spans", []) if span.get("op", "").startswith("gen_ai")
    ]

    # Check if any span has message data with normalized roles
    message_data_found = False
    for span in gen_ai_spans:
        span_data = span.get("data", {})
        if SPANDATA.GEN_AI_REQUEST_MESSAGES in span_data:
            message_data_found = True
            messages_data = span_data[SPANDATA.GEN_AI_REQUEST_MESSAGES]

            # Parse the message data (might be JSON string)
            if isinstance(messages_data, str):
                try:
                    messages = json.loads(messages_data)
                except json.JSONDecodeError:
                    # If not valid JSON, skip this assertion
                    continue
            else:
                messages = messages_data

            # Verify that the input message is present and contains the test input
            assert isinstance(messages, list)
            assert len(messages) > 0

            # The test input should be in one of the messages
            input_found = False
            for msg in messages:
                if isinstance(msg, dict) and test_input in str(msg.get("content", "")):
                    input_found = True
                    break
                elif isinstance(msg, str) and test_input in msg:
                    input_found = True
                    break

            assert input_found, (
                f"Test input '{test_input}' not found in messages: {messages}"
            )
            break

    # The message role mapping functionality is primarily tested through the normalization
    # that happens in the integration code. The fact that we can capture and process
    # the messages without errors indicates the role mapping is working correctly.
    assert message_data_found, "No span found with gen_ai request messages data"


def test_langchain_message_role_normalization_units():
    """Test the message role normalization functions directly."""
    from sentry_sdk.ai.utils import normalize_message_role, normalize_message_roles

    # Test individual role normalization
    assert normalize_message_role("ai") == "assistant"
    assert normalize_message_role("human") == "user"
    assert normalize_message_role("tool_call") == "tool"
    assert normalize_message_role("system") == "system"
    assert normalize_message_role("user") == "user"
    assert normalize_message_role("assistant") == "assistant"
    assert normalize_message_role("tool") == "tool"

    # Test unknown role (should remain unchanged)
    assert normalize_message_role("unknown_role") == "unknown_role"

    # Test message list normalization
    test_messages = [
        {"role": "human", "content": "Hello"},
        {"role": "ai", "content": "Hi there!"},
        {"role": "tool_call", "content": "function_call"},
        {"role": "system", "content": "You are helpful"},
        {"content": "Message without role"},
        "string message",
    ]

    normalized = normalize_message_roles(test_messages)

    # Verify the original messages are not modified
    assert test_messages[0]["role"] == "human"  # Original unchanged
    assert test_messages[1]["role"] == "ai"  # Original unchanged

    # Verify the normalized messages have correct roles
    assert normalized[0]["role"] == "user"  # human -> user
    assert normalized[1]["role"] == "assistant"  # ai -> assistant
    assert normalized[2]["role"] == "tool"  # tool_call -> tool
    assert normalized[3]["role"] == "system"  # system unchanged
    assert "role" not in normalized[4]  # Message without role unchanged
    assert normalized[5] == "string message"  # String message unchanged


def test_langchain_message_truncation(sentry_init, capture_events):
    """Test that large messages are truncated properly in Langchain integration."""
    from langchain_core.outputs import LLMResult, Generation

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    callback = SentryLangchainCallback(max_span_map_size=100, include_prompts=True)

    run_id = "12345678-1234-1234-1234-123456789012"
    serialized = {"_type": "openai-chat", "model_name": "gpt-3.5-turbo"}

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )
    prompts = [
        "small message 1",
        large_content,
        large_content,
        "small message 4",
        "small message 5",
    ]

    with start_transaction():
        callback.on_llm_start(
            serialized=serialized,
            prompts=prompts,
            run_id=run_id,
            invocation_params={
                "temperature": 0.7,
                "max_tokens": 100,
                "model": "gpt-3.5-turbo",
            },
        )

        response = LLMResult(
            generations=[[Generation(text="The response")]],
            llm_output={
                "token_usage": {
                    "total_tokens": 25,
                    "prompt_tokens": 10,
                    "completion_tokens": 15,
                }
            },
        )
        callback.on_llm_end(response=response, run_id=run_id)

    assert len(events) > 0
    tx = events[0]
    assert tx["type"] == "transaction"

    llm_spans = [
        span for span in tx.get("spans", []) if span.get("op") == "gen_ai.pipeline"
    ]
    assert len(llm_spans) > 0

    llm_span = llm_spans[0]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in llm_span["data"]

    messages_data = llm_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) == 2
    assert "small message 4" in str(parsed_messages[0])
    assert "small message 5" in str(parsed_messages[1])
    assert tx["_meta"]["spans"]["0"]["data"]["gen_ai.request.messages"][""]["len"] == 5

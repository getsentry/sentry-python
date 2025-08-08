from typing import List, Optional, Any, Iterator
from unittest import mock
from unittest.mock import Mock

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


def tiktoken_encoding_if_installed():
    try:
        import tiktoken  # type: ignore # noqa # pylint: disable=unused-import

        return "cl100k_base"
    except ImportError:
        return None


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
                tiktoken_encoding_name=tiktoken_encoding_if_installed(),
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
                    message=AIMessageChunk(content="5"),
                    generation_info={"finish_reason": "function_call"},
                ),
            ],
            [
                ChatGenerationChunk(
                    text="The word eudca has 5 letters.",
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(content="The word eudca has 5 letters."),
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
    assert len(list(x for x in tx["spans"] if x["op"] == "gen_ai.run")) > 0

    if use_unknown_llm_type:
        assert "gen_ai.usage.input_tokens" in chat_spans[0]["data"]
        assert "gen_ai.usage.total_tokens" in chat_spans[0]["data"]
    else:
        # important: to avoid double counting, we do *not* measure
        # tokens used if we have an explicit integration (e.g. OpenAI)
        assert "measurements" not in chat_spans[0]

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
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in tool_exec_span.get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in tool_exec_span.get("data", {})


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
    stream_result_mock = Mock(side_effect=Exception("API rate limit error"))
    llm = MockOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    with start_transaction(), pytest.raises(Exception):
        list(agent_executor.stream({"input": "How many letters in the word eudca"}))

    error = events[0]
    assert error["level"] == "error"


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
                    message=AIMessageChunk(content="5"),
                    generation_info={"finish_reason": "function_call"},
                ),
            ],
            [
                ChatGenerationChunk(
                    text="The word eudca has 5 letters.",
                    type="ChatGenerationChunk",
                    message=AIMessageChunk(content="The word eudca has 5 letters."),
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
    assert (
        callback1.span_map is not callback2.span_map
    ), "span_map should be an instance variable, not shared between instances"


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

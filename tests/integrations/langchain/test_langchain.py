from pathlib import Path
from typing import List, Optional, Any, Iterator
from unittest.mock import Mock

import pytest
from langchain_community.chat_models import ChatOpenAI
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from sentry_sdk import start_transaction
from sentry_sdk.integrations.langchain import LangchainIntegration
from langchain.agents import tool, AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


@tool
def get_word_length(word: str) -> int:
    """Returns the length of a word."""
    return len(word)


global stream_result_mock  # type: Mock


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


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_langchain_agent(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[LangchainIntegration(include_prompts=include_prompts)],
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
        openai_api_key=open(Path.home() / "open_ai_key").read().strip(),
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    with start_transaction():
        list(agent_executor.stream({"input": "How many letters in the word eudca"}))

    tx = events[0]
    assert tx["type"] == "transaction"
    chat_spans = list(
        x for x in tx["spans"] if x["op"] == "ai.chat_completions.create.langchain"
    )
    tool_exec_span = next(x for x in tx["spans"] if x["op"] == "ai.tool.langchain")

    assert len(chat_spans) == 2

    # We can't guarantee anything about the "shape" of the langchain execution graph
    assert len(list(x for x in tx["spans"] if x["op"] == "ai.run.langchain")) > 0

    if send_default_pii and include_prompts:
        assert (
            "You are very powerful"
            in chat_spans[0]["data"]["ai.input_messages"][0][0]["content"]
        )
        assert "5" in chat_spans[0]["data"]["ai.responses"][0][0]
        assert "word" in tool_exec_span["data"]["ai.input_messages"]
        assert "5" in tool_exec_span["data"]["ai.responses"][0]
        assert (
            "You are very powerful"
            in chat_spans[1]["data"]["ai.input_messages"][0][0]["content"]
        )
        assert "5" in chat_spans[1]["data"]["ai.responses"][0][0]
    else:
        assert "ai.input_messages" not in chat_spans[0].get("data", {})
        assert "ai.responses" not in chat_spans[0].get("data", {})
        assert "ai.input_messages" not in chat_spans[1].get("data", {})
        assert "ai.responses" not in chat_spans[1].get("data", {})
        assert "ai.input_messages" not in tool_exec_span.get("data", {})
        assert "ai.responses" not in tool_exec_span.get("data", {})

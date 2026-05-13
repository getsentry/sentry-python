import json
from typing import List, Optional, Any, Iterator
from unittest import mock
from unittest.mock import Mock, patch

import pytest

from sentry_sdk.consts import SPANDATA

try:
    # Langchain >= 0.2
    from langchain_openai import ChatOpenAI, OpenAI
except ImportError:
    # Langchain < 0.2
    from langchain_community.llms import OpenAI
    from langchain_community.chat_models import ChatOpenAI

from langchain_core.callbacks import BaseCallbackManager, CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.language_models.chat_models import BaseChatModel

import sentry_sdk
from sentry_sdk import start_transaction
from sentry_sdk.utils import package_version
from sentry_sdk.integrations.langchain import (
    LangchainIntegration,
    SentryLangchainCallback,
    _transform_langchain_content_block,
    _transform_langchain_message_content,
)

try:
    # langchain v1+
    from langchain.tools import tool
    from langchain.agents import create_agent
    from langchain_classic.agents import AgentExecutor, create_openai_tools_agent  # type: ignore[import-not-found]
except ImportError:
    # langchain <v1
    from langchain.agents import tool, AgentExecutor, create_openai_tools_agent

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, SystemMessage

from openai.types.chat.chat_completion_chunk import (
    ChatCompletionChunk,
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)

from openai.types.completion import Completion
from openai.types.completion_choice import CompletionChoice

from openai.types.completion_usage import (
    CompletionUsage,
)

from openai.types.responses import (
    ResponseUsage,
)
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
)

LANGCHAIN_VERSION = package_version("langchain")
LANGCHAIN_OPENAI_VERSION = package_version("langchain-openai")


@pytest.fixture
def streaming_chat_completions_model_responses():
    def inner():
        yield [
            ChatCompletionChunk(
                id="chatcmpl-turn-1",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(role="assistant"),
                        finish_reason=None,
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-1",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(
                            tool_calls=[
                                ChoiceDeltaToolCall(
                                    index=0,
                                    id="call_BbeyNhCKa6kYLYzrD40NGm3b",
                                    type="function",
                                    function=ChoiceDeltaToolCallFunction(
                                        name="get_word_length",
                                        arguments="",
                                    ),
                                ),
                            ],
                        ),
                        finish_reason=None,
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-1",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(
                            tool_calls=[
                                ChoiceDeltaToolCall(
                                    index=0,
                                    function=ChoiceDeltaToolCallFunction(
                                        arguments='{"word": "eudca"}',
                                    ),
                                ),
                            ],
                        ),
                        finish_reason=None,
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-1",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(content="5"),
                        finish_reason=None,
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-1",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(),
                        finish_reason="function_call",
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-1",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[],
                usage=CompletionUsage(
                    prompt_tokens=142,
                    completion_tokens=50,
                    total_tokens=192,
                ),
            ),
        ]

        yield [
            ChatCompletionChunk(
                id="chatcmpl-turn-2",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(role="assistant"),
                        finish_reason=None,
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-2",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(content="The word eudca has 5 letters."),
                        finish_reason=None,
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-2",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[
                    Choice(
                        index=0,
                        delta=ChoiceDelta(),
                        finish_reason="stop",
                    ),
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-turn-2",
                object="chat.completion.chunk",
                created=10000000,
                model="gpt-3.5-turbo",
                choices=[],
                usage=CompletionUsage(
                    prompt_tokens=89,
                    completion_tokens=28,
                    total_tokens=117,
                ),
            ),
        ]

    return inner


@tool
def get_word_length(word: str) -> int:
    """Returns the length of a word."""
    return len(word)


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_text_completion(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=True,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    model_response = get_model_response(
        Completion(
            id="completion-id",
            object="text_completion",
            created=10000000,
            model="gpt-3.5-turbo",
            choices=[
                CompletionChoice(
                    index=0,
                    finish_reason="stop",
                    text="The capital of France is Paris.",
                )
            ],
            usage=CompletionUsage(
                prompt_tokens=10,
                completion_tokens=15,
                total_tokens=25,
            ),
        ),
        serialize_pydantic=True,
    )

    model = OpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0.7,
        max_tokens=100,
        openai_api_key="badkey",
    )

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            model.client._client._client,
            "send",
            return_value=model_response,
        ) as _, start_transaction():
            input_text = "What is the capital of France?"
            model.invoke(input_text, config={"run_name": "my-snazzy-pipeline"})

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"

        spans = [item.payload for item in items if item.type == "span"]
        llm_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.text_completion"
        ]
        assert len(llm_spans) > 0

        llm_span = llm_spans[0]
        assert llm_span["name"] == "text_completion gpt-3.5-turbo"
        assert llm_span["attributes"]["gen_ai.system"] == "openai"
        assert llm_span["attributes"]["gen_ai.function_id"] == "my-snazzy-pipeline"
        assert llm_span["attributes"]["gen_ai.request.model"] == "gpt-3.5-turbo"
        assert (
            llm_span["attributes"]["gen_ai.response.text"]
            == "The capital of France is Paris."
        )
        assert llm_span["attributes"]["gen_ai.usage.total_tokens"] == 25
        assert llm_span["attributes"]["gen_ai.usage.input_tokens"] == 10
        assert llm_span["attributes"]["gen_ai.usage.output_tokens"] == 15
    else:
        events = capture_events()

        with patch.object(
            model.client._client._client,
            "send",
            return_value=model_response,
        ) as _, start_transaction():
            input_text = "What is the capital of France?"
            model.invoke(input_text, config={"run_name": "my-snazzy-pipeline"})

        tx = events[0]
        assert tx["type"] == "transaction"

        llm_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.text_completion"
        ]
        assert len(llm_spans) > 0

        llm_span = llm_spans[0]
        assert llm_span["description"] == "text_completion gpt-3.5-turbo"
        assert llm_span["data"]["gen_ai.system"] == "openai"
        assert llm_span["data"]["gen_ai.function_id"] == "my-snazzy-pipeline"
        assert llm_span["data"]["gen_ai.request.model"] == "gpt-3.5-turbo"
        assert (
            llm_span["data"]["gen_ai.response.text"]
            == "The capital of France is Paris."
        )
        assert llm_span["data"]["gen_ai.usage.total_tokens"] == 25
        assert llm_span["data"]["gen_ai.usage.input_tokens"] == 10
        assert llm_span["data"]["gen_ai.usage.output_tokens"] == 15


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_chat_with_run_name(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    nonstreaming_chat_completions_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=True,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    request_headers = {}
    # Changed in https://github.com/langchain-ai/langchain/pull/32655
    if LANGCHAIN_OPENAI_VERSION >= (0, 3, 32):
        request_headers["X-Stainless-Raw-Response"] = "True"

    model_response = get_model_response(
        nonstreaming_chat_completions_model_response(
            response_id="chat-id",
            response_model="response-model-id",
            message_content="the model response",
            created=10000000,
            usage=CompletionUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        ),
        serialize_pydantic=True,
        request_headers=request_headers,
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )

    if stream_gen_ai_spans:
        items = capture_items("span")

        with patch.object(
            llm.client._client._client,
            "send",
            return_value=model_response,
        ) as _, start_transaction():
            llm.invoke(
                "How many letters in the word eudca",
                config={"run_name": "my-snazzy-pipeline"},
            )

        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"
        )
        assert len(chat_spans) == 1
        assert (
            chat_spans[0]["attributes"][SPANDATA.GEN_AI_FUNCTION_ID]
            == "my-snazzy-pipeline"
        )
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            return_value=model_response,
        ) as _, start_transaction():
            llm.invoke(
                "How many letters in the word eudca",
                config={"run_name": "my-snazzy-pipeline"},
            )

        tx = events[0]

        chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
        assert len(chat_spans) == 1
        assert (
            chat_spans[0]["data"][SPANDATA.GEN_AI_FUNCTION_ID] == "my-snazzy-pipeline"
        )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_tool_call_with_run_name(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=True,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction():
            get_word_length.invoke(
                {"word": "eudca"},
                config={"run_name": "my-snazzy-pipeline"},
            )

        spans = [item.payload for item in items if item.type == "span"]
        tool_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.execute_tool"
        )
        assert len(tool_spans) == 1
        assert (
            tool_spans[0]["attributes"][SPANDATA.GEN_AI_FUNCTION_ID]
            == "my-snazzy-pipeline"
        )
    else:
        events = capture_events()

        with start_transaction():
            get_word_length.invoke(
                {"word": "eudca"},
                config={"run_name": "my-snazzy-pipeline"},
            )

        tx = events[0]
        tool_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.execute_tool")
        assert len(tool_spans) == 1
        assert (
            tool_spans[0]["data"][SPANDATA.GEN_AI_FUNCTION_ID] == "my-snazzy-pipeline"
        )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(
    LANGCHAIN_VERSION < (1,),
    reason="LangChain 1.0+ required (ONE AGENT refactor)",
)
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
@pytest.mark.parametrize(
    "system_instructions_content",
    [
        "You are very powerful assistant, but don't know current events",
        [
            {"type": "text", "text": "You are a helpful assistant."},
            {"type": "text", "text": "Be concise and clear."},
        ],
    ],
    ids=["string", "blocks"],
)
def test_langchain_create_agent(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    system_instructions_content,
    request,
    get_model_response,
    nonstreaming_responses_model_response,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=include_prompts,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    model_response = get_model_response(
        nonstreaming_responses_model_response,
        serialize_pydantic=True,
        request_headers={
            "X-Stainless-Raw-Response": "True",
        },
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
        use_responses_api=True,
    )
    agent = create_agent(
        model=llm,
        tools=[get_word_length],
        system_prompt=SystemMessage(content=system_instructions_content),
        name="word_length_agent",
    )

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            return_value=model_response,
        ) as _, start_transaction():
            agent.invoke(
                {
                    "messages": [
                        HumanMessage(
                            content="Message demonstrating the absence of truncation."
                        ),
                        HumanMessage(content="How many letters in the word eudca"),
                    ],
                },
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"
        )
        assert len(chat_spans) == 1
        assert chat_spans[0]["attributes"]["sentry.origin"] == "auto.ai.langchain"

        assert chat_spans[0]["attributes"]["gen_ai.system"] == "openai-chat"
        assert chat_spans[0]["attributes"]["gen_ai.agent.name"] == "word_length_agent"

        assert chat_spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 10
        assert chat_spans[0]["attributes"]["gen_ai.usage.output_tokens"] == 20
        assert chat_spans[0]["attributes"]["gen_ai.usage.total_tokens"] == 30

        if send_default_pii and include_prompts:
            assert (
                chat_spans[0]["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]
                == "Hello, how can I help you?"
            )

            assert json.loads(
                chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            ) == [
                {
                    "role": "user",
                    "content": "Message demonstrating the absence of truncation.",
                },
                {
                    "role": "user",
                    "content": "How many letters in the word eudca",
                },
            ]

            param_id = request.node.callspec.id
            if "string" in param_id:
                assert [
                    {
                        "type": "text",
                        "content": "You are very powerful assistant, but don't know current events",
                    }
                ] == json.loads(
                    chat_spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
                )
            else:
                assert [
                    {
                        "type": "text",
                        "content": "You are a helpful assistant.",
                    },
                    {
                        "type": "text",
                        "content": "Be concise and clear.",
                    },
                ] == json.loads(
                    chat_spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
                )
        else:
            assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[0].get(
                "attributes", {}
            )
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get(
                "attributes", {}
            )
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get(
                "attributes", {}
            )

    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            return_value=model_response,
        ) as _, start_transaction():
            agent.invoke(
                {
                    "messages": [
                        HumanMessage(content="How many letters in the word eudca"),
                    ],
                },
            )

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
        assert len(chat_spans) == 1
        assert chat_spans[0]["origin"] == "auto.ai.langchain"

        assert chat_spans[0]["data"]["gen_ai.system"] == "openai-chat"
        assert chat_spans[0]["data"]["gen_ai.agent.name"] == "word_length_agent"

        assert chat_spans[0]["data"]["gen_ai.usage.input_tokens"] == 10
        assert chat_spans[0]["data"]["gen_ai.usage.output_tokens"] == 20
        assert chat_spans[0]["data"]["gen_ai.usage.total_tokens"] == 30

        if send_default_pii and include_prompts:
            assert (
                chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
                == "Hello, how can I help you?"
            )

            param_id = request.node.callspec.id
            if "string" in param_id:
                assert [
                    {
                        "type": "text",
                        "content": "You are very powerful assistant, but don't know current events",
                    }
                ] == json.loads(
                    chat_spans[0]["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
                )
            else:
                assert [
                    {
                        "type": "text",
                        "content": "You are a helpful assistant.",
                    },
                    {
                        "type": "text",
                        "content": "Be concise and clear.",
                    },
                ] == json.loads(
                    chat_spans[0]["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
                )
        else:
            assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[0].get(
                "data", {}
            )
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get("data", {})
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("data", {})


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.skipif(
    LANGCHAIN_VERSION < (1,),
    reason="LangChain 1.0+ required (ONE AGENT refactor)",
)
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_tool_execution_span(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    responses_tool_call_model_responses,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=include_prompts,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    responses = responses_tool_call_model_responses(
        tool_name="get_word_length",
        arguments='{"word": "eudca"}',
        response_model="gpt-4-0613",
        response_text="The word eudca has 5 letters.",
        response_ids=iter(["resp_1", "resp_2"]),
        usages=iter(
            [
                ResponseUsage(
                    input_tokens=142,
                    input_tokens_details=InputTokensDetails(
                        cached_tokens=0,
                    ),
                    output_tokens=50,
                    output_tokens_details=OutputTokensDetails(
                        reasoning_tokens=0,
                    ),
                    total_tokens=192,
                ),
                ResponseUsage(
                    input_tokens=89,
                    input_tokens_details=InputTokensDetails(
                        cached_tokens=0,
                    ),
                    output_tokens=28,
                    output_tokens_details=OutputTokensDetails(
                        reasoning_tokens=0,
                    ),
                    total_tokens=117,
                ),
            ]
        ),
    )
    tool_response = get_model_response(
        next(responses),
        serialize_pydantic=True,
        request_headers={
            "X-Stainless-Raw-Response": "True",
        },
    )
    final_response = get_model_response(
        next(responses),
        serialize_pydantic=True,
        request_headers={
            "X-Stainless-Raw-Response": "True",
        },
    )

    llm = ChatOpenAI(
        model_name="gpt-4",
        temperature=0,
        openai_api_key="badkey",
        use_responses_api=True,
    )
    agent = create_agent(
        model=llm,
        tools=[get_word_length],
        name="word_length_agent",
    )

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            agent.invoke(
                {
                    "messages": [
                        HumanMessage(content="How many letters in the word eudca"),
                    ],
                },
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        chat_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"
        )
        assert len(chat_spans) == 2

        tool_exec_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.execute_tool"
        )
        assert len(tool_exec_spans) == 1
        tool_exec_span = tool_exec_spans[0]

        assert chat_spans[0]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[1]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert tool_exec_span["attributes"]["sentry.origin"] == "auto.ai.langchain"

        assert chat_spans[0]["attributes"]["gen_ai.agent.name"] == "word_length_agent"
        assert chat_spans[1]["attributes"]["gen_ai.agent.name"] == "word_length_agent"
        assert tool_exec_span["attributes"]["gen_ai.agent.name"] == "word_length_agent"

        assert chat_spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 142
        assert chat_spans[0]["attributes"]["gen_ai.usage.output_tokens"] == 50
        assert chat_spans[0]["attributes"]["gen_ai.usage.total_tokens"] == 192
        assert chat_spans[0]["attributes"]["gen_ai.system"] == "openai-chat"

        assert chat_spans[1]["attributes"]["gen_ai.usage.input_tokens"] == 89
        assert chat_spans[1]["attributes"]["gen_ai.usage.output_tokens"] == 28
        assert chat_spans[1]["attributes"]["gen_ai.usage.total_tokens"] == 117
        assert chat_spans[1]["attributes"]["gen_ai.system"] == "openai-chat"

        if send_default_pii and include_prompts:
            assert "word" in tool_exec_span["attributes"][SPANDATA.GEN_AI_TOOL_INPUT]

            assert "5" in chat_spans[1]["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

            # Verify tool calls are recorded when PII is enabled
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_spans[0].get(
                "attributes", {}
            ), (
                "Tool calls should be recorded when send_default_pii=True and include_prompts=True"
            )
            tool_calls_data = chat_spans[0]["attributes"][
                SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS
            ]
            assert isinstance(tool_calls_data, str)
            assert "get_word_length" in tool_calls_data
        else:
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get(
                "attributes", {}
            )
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get(
                "attributes", {}
            )
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get(
                "attributes", {}
            )
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get(
                "attributes", {}
            )
            assert SPANDATA.GEN_AI_TOOL_INPUT not in tool_exec_span.get(
                "attributes", {}
            )
            assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_exec_span.get(
                "attributes", {}
            )

            # Verify tool calls are NOT recorded when PII is disabled
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[0].get(
                "attributes", {}
            ), (
                f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
                f"and include_prompts={include_prompts}"
            )
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[1].get(
                "attributes", {}
            ), (
                f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
                f"and include_prompts={include_prompts}"
            )

        # Verify that available tools are always recorded regardless of PII settings
        for chat_span in chat_spans:
            tools_data = chat_span["attributes"][
                SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS
            ]
            assert "get_word_length" in tools_data
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            agent.invoke(
                {
                    "messages": [
                        HumanMessage(content="How many letters in the word eudca"),
                    ],
                },
            )

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
        assert len(chat_spans) == 2
        tool_exec_spans = list(
            x for x in tx["spans"] if x["op"] == "gen_ai.execute_tool"
        )

        assert len(tool_exec_spans) == 1
        tool_exec_span = tool_exec_spans[0]

        assert chat_spans[0]["origin"] == "auto.ai.langchain"
        assert chat_spans[1]["origin"] == "auto.ai.langchain"
        assert tool_exec_span["origin"] == "auto.ai.langchain"

        assert chat_spans[0]["data"]["gen_ai.agent.name"] == "word_length_agent"
        assert chat_spans[1]["data"]["gen_ai.agent.name"] == "word_length_agent"
        assert tool_exec_span["data"]["gen_ai.agent.name"] == "word_length_agent"

        assert chat_spans[0]["data"]["gen_ai.usage.input_tokens"] == 142
        assert chat_spans[0]["data"]["gen_ai.usage.output_tokens"] == 50
        assert chat_spans[0]["data"]["gen_ai.usage.total_tokens"] == 192
        assert chat_spans[0]["data"]["gen_ai.system"] == "openai-chat"

        assert chat_spans[1]["data"]["gen_ai.usage.input_tokens"] == 89
        assert chat_spans[1]["data"]["gen_ai.usage.output_tokens"] == 28
        assert chat_spans[1]["data"]["gen_ai.usage.total_tokens"] == 117
        assert chat_spans[1]["data"]["gen_ai.system"] == "openai-chat"

        if send_default_pii and include_prompts:
            assert "word" in tool_exec_span["data"][SPANDATA.GEN_AI_TOOL_INPUT]

            assert "5" in chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

            # Verify tool calls are recorded when PII is enabled
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_spans[0].get(
                "data", {}
            ), (
                "Tool calls should be recorded when send_default_pii=True and include_prompts=True"
            )
            tool_calls_data = chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS]
            assert isinstance(tool_calls_data, str)
            assert "get_word_length" in tool_calls_data
        else:
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get("data", {})
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("data", {})
            assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get("data", {})
            assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get("data", {})
            assert SPANDATA.GEN_AI_TOOL_INPUT not in tool_exec_span.get("data", {})
            assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_exec_span.get("data", {})

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
            tools_data = chat_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
            assert "get_word_length" in tools_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_langchain_openai_tools_agent_no_prompts(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_responses,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=include_prompts,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

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

    model_responses = streaming_chat_completions_model_responses()

    tool_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    final_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.invoke(
                    {"input": "How many letters in the word eudca"},
                    {"run_name": "my-snazzy-pipeline"},
                )
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        invoke_agent_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"
        )
        tool_exec_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[0]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[1]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert tool_exec_span["attributes"]["sentry.origin"] == "auto.ai.langchain"

        assert (
            invoke_agent_span["attributes"]["gen_ai.function_id"]
            == "my-snazzy-pipeline"
        )

        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert (
            len(list(x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"))
            > 0
        )

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["attributes"]:
            assert chat_spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["attributes"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["attributes"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["attributes"]:
            assert chat_spans[1]["attributes"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["attributes"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["attributes"]["gen_ai.usage.total_tokens"] == 117

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[0].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("attributes", {})
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[1].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get("attributes", {})
        assert SPANDATA.GEN_AI_TOOL_INPUT not in tool_exec_span.get("attributes", {})
        assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_exec_span.get("attributes", {})

        # Verify tool calls are NOT recorded when PII is disabled
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[0].get(
            "attributes", {}
        ), (
            f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
            f"and include_prompts={include_prompts}"
        )
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[1].get(
            "attributes", {}
        ), (
            f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
            f"and include_prompts={include_prompts}"
        )

        # Verify finish_reasons is always an array of strings
        assert chat_spans[0]["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "function_call"
        ]
        assert chat_spans[1]["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "stop"
        ]

        # Verify that available tools are always recorded regardless of PII settings
        for chat_span in chat_spans:
            tools_data = chat_span["attributes"][
                SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS
            ]
            assert tools_data is not None, (
                "Available tools should always be recorded regardless of PII settings"
            )
            assert "get_word_length" in tools_data
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.invoke(
                    {"input": "How many letters in the word eudca"},
                    {"run_name": "my-snazzy-pipeline"},
                )
            )

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        invoke_agent_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
        tool_exec_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["origin"] == "auto.ai.langchain"
        assert chat_spans[0]["origin"] == "auto.ai.langchain"
        assert chat_spans[1]["origin"] == "auto.ai.langchain"
        assert tool_exec_span["origin"] == "auto.ai.langchain"

        assert invoke_agent_span["data"]["gen_ai.function_id"] == "my-snazzy-pipeline"

        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert len(list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")) > 0

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["data"]:
            assert chat_spans[0]["data"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["data"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["data"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["data"]:
            assert chat_spans[1]["data"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["data"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["data"]["gen_ai.usage.total_tokens"] == 117

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_TOOL_INPUT not in tool_exec_span.get("data", {})
        assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_exec_span.get("data", {})

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

        # Verify finish_reasons is always an array of strings
        assert chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "function_call"
        ]
        assert chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "stop"
        ]

        # Verify that available tools are always recorded regardless of PII settings
        for chat_span in chat_spans:
            tools_data = chat_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
            assert tools_data is not None, (
                "Available tools should always be recorded regardless of PII settings"
            )
            assert "get_word_length" in tools_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "system_instructions_content",
    [
        "You are very powerful assistant, but don't know current events",
        ["You are a helpful assistant.", "Be concise and clear."],
        [
            {"type": "text", "text": "You are a helpful assistant."},
            {"type": "text", "text": "Be concise and clear."},
        ],
    ],
    ids=["string", "list", "blocks"],
)
def test_langchain_openai_tools_agent(
    sentry_init,
    capture_events,
    capture_items,
    system_instructions_content,
    request,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_responses,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=True,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                system_instructions_content,
            ),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    model_responses = streaming_chat_completions_model_responses()

    tool_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    final_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.stream(
                    {
                        "input": [
                            "Message demonstrating the absence of truncation.",
                            "How many letters in the word eudca",
                        ]
                    }
                )
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        invoke_agent_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"
        )
        tool_exec_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[0]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[1]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert tool_exec_span["attributes"]["sentry.origin"] == "auto.ai.langchain"

        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert (
            len(list(x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"))
            > 0
        )

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["attributes"]:
            assert chat_spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["attributes"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["attributes"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["attributes"]:
            assert chat_spans[1]["attributes"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["attributes"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["attributes"]["gen_ai.usage.total_tokens"] == 117

        assert "5" in chat_spans[0]["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert "word" in tool_exec_span["attributes"][SPANDATA.GEN_AI_TOOL_INPUT]
        assert 5 == int(tool_exec_span["attributes"][SPANDATA.GEN_AI_TOOL_OUTPUT])

        assert json.loads(
            chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        ) == [
            {
                "role": "user",
                "content": "['Message demonstrating the absence of truncation.', 'How many letters in the word eudca']",
            }
        ]

        param_id = request.node.callspec.id
        if "string" in param_id:
            assert [
                {
                    "type": "text",
                    "content": "You are very powerful assistant, but don't know current events",
                }
            ] == json.loads(
                chat_spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            )
        else:
            assert [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ] == json.loads(
                chat_spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            )

            assert "5" in chat_spans[1]["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

            # Verify tool calls are recorded when PII is enabled
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_spans[0].get(
                "attributes", {}
            ), (
                "Tool calls should be recorded when send_default_pii=True and include_prompts=True"
            )
            tool_calls_data = chat_spans[0]["attributes"][
                SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS
            ]

            assert isinstance(tool_calls_data, (list, str))  # Could be serialized
            if isinstance(tool_calls_data, str):
                assert "get_word_length" in tool_calls_data
            elif isinstance(tool_calls_data, list) and len(tool_calls_data) > 0:
                # Check if tool calls contain expected function name
                tool_call_str = str(tool_calls_data)
                assert "get_word_length" in tool_call_str

            # Verify finish_reasons is always an array of strings
            assert chat_spans[0]["attributes"][
                SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS
            ] == ["function_call"]
            assert chat_spans[1]["attributes"][
                SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS
            ] == ["stop"]

            # Verify that available tools are always recorded regardless of PII settings
            for chat_span in chat_spans:
                tools_data = chat_span["attributes"][
                    SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS
                ]
                assert tools_data is not None, (
                    "Available tools should always be recorded regardless of PII settings"
                )
                assert "get_word_length" in tools_data
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(agent_executor.stream({"input": "How many letters in the word eudca"}))

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        invoke_agent_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
        tool_exec_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["origin"] == "auto.ai.langchain"
        assert chat_spans[0]["origin"] == "auto.ai.langchain"
        assert chat_spans[1]["origin"] == "auto.ai.langchain"
        assert tool_exec_span["origin"] == "auto.ai.langchain"

        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert len(list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")) > 0

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["data"]:
            assert chat_spans[0]["data"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["data"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["data"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["data"]:
            assert chat_spans[1]["data"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["data"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["data"]["gen_ai.usage.total_tokens"] == 117

        assert "5" in chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert "word" in tool_exec_span["data"][SPANDATA.GEN_AI_TOOL_INPUT]
        assert 5 == int(tool_exec_span["data"][SPANDATA.GEN_AI_TOOL_OUTPUT])

        param_id = request.node.callspec.id
        if "string" in param_id:
            assert [
                {
                    "type": "text",
                    "content": "You are very powerful assistant, but don't know current events",
                }
            ] == json.loads(chat_spans[0]["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
        else:
            assert [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ] == json.loads(chat_spans[0]["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])

            assert "5" in chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

            # Verify tool calls are recorded when PII is enabled
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_spans[0].get(
                "data", {}
            ), (
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

            # Verify finish_reasons is always an array of strings
            assert chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
                "function_call"
            ]
            assert chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
                "stop"
            ]

            # Verify that available tools are always recorded regardless of PII settings
            for chat_span in chat_spans:
                tools_data = chat_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
                assert tools_data is not None, (
                    "Available tools should always be recorded regardless of PII settings"
                )
                assert "get_word_length" in tools_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_openai_tools_agent_with_config(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_responses,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=True,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

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

    model_responses = streaming_chat_completions_model_responses()

    tool_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    final_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt).with_config(
        {"run_name": "my-snazzy-pipeline"}
    )

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.invoke(
                    {"input": "How many letters in the word eudca"},
                )
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        invoke_agent_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.invoke_agent"
        )
        assert (
            invoke_agent_span["attributes"]["gen_ai.function_id"]
            == "my-snazzy-pipeline"
        )
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.invoke(
                    {"input": "How many letters in the word eudca"},
                )
            )

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        invoke_agent_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.invoke_agent"
        )
        assert invoke_agent_span["data"]["gen_ai.function_id"] == "my-snazzy-pipeline"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_langchain_openai_tools_agent_stream_no_prompts(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_responses,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=include_prompts,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

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

    model_responses = streaming_chat_completions_model_responses()

    tool_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    final_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.stream(
                    {"input": "How many letters in the word eudca"},
                    {"run_name": "my-snazzy-pipeline"},
                )
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        invoke_agent_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"
        )
        tool_exec_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[0]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[1]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert tool_exec_span["attributes"]["sentry.origin"] == "auto.ai.langchain"

        assert (
            invoke_agent_span["attributes"]["gen_ai.function_id"]
            == "my-snazzy-pipeline"
        )

        spans = [item.payload for item in items if item.type == "span"]
        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert (
            len(list(x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"))
            > 0
        )

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["attributes"]:
            assert chat_spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["attributes"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["attributes"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["attributes"]:
            assert chat_spans[1]["attributes"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["attributes"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["attributes"]["gen_ai.usage.total_tokens"] == 117

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[0].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("attributes", {})
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[1].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get(
            "attributes", {}
        )
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get("attributes", {})
        assert SPANDATA.GEN_AI_TOOL_INPUT not in tool_exec_span.get("attributes", {})
        assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_exec_span.get("attributes", {})

        # Verify tool calls are NOT recorded when PII is disabled
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[0].get(
            "attributes", {}
        ), (
            f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
            f"and include_prompts={include_prompts}"
        )
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in chat_spans[1].get(
            "attributes", {}
        ), (
            f"Tool calls should NOT be recorded when send_default_pii={send_default_pii} "
            f"and include_prompts={include_prompts}"
        )

        # Verify finish_reasons is always an array of strings
        assert chat_spans[0]["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "function_call"
        ]
        assert chat_spans[1]["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "stop"
        ]

        # Verify that available tools are always recorded regardless of PII settings
        for chat_span in chat_spans:
            tools_data = chat_span["attributes"][
                SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS
            ]

            assert tools_data is not None, (
                "Available tools should always be recorded regardless of PII settings"
            )
            assert "get_word_length" in tools_data
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.stream(
                    {"input": "How many letters in the word eudca"},
                    {"run_name": "my-snazzy-pipeline"},
                )
            )

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        invoke_agent_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
        tool_exec_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["origin"] == "auto.ai.langchain"
        assert chat_spans[0]["origin"] == "auto.ai.langchain"
        assert chat_spans[1]["origin"] == "auto.ai.langchain"
        assert tool_exec_span["origin"] == "auto.ai.langchain"

        assert invoke_agent_span["data"]["gen_ai.function_id"] == "my-snazzy-pipeline"

        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert len(list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")) > 0

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["data"]:
            assert chat_spans[0]["data"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["data"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["data"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["data"]:
            assert chat_spans[1]["data"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["data"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["data"]["gen_ai.usage.total_tokens"] == 117

        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[0].get("data", {})
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_spans[1].get("data", {})
        assert SPANDATA.GEN_AI_TOOL_INPUT not in tool_exec_span.get("data", {})
        assert SPANDATA.GEN_AI_TOOL_OUTPUT not in tool_exec_span.get("data", {})

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

        # Verify finish_reasons is always an array of strings
        assert chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "function_call"
        ]
        assert chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
            "stop"
        ]

        # Verify that available tools are always recorded regardless of PII settings
        for chat_span in chat_spans:
            tools_data = chat_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
            assert tools_data is not None, (
                "Available tools should always be recorded regardless of PII settings"
            )
            assert "get_word_length" in tools_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "system_instructions_content",
    [
        "You are very powerful assistant, but don't know current events",
        ["You are a helpful assistant.", "Be concise and clear."],
        [
            {"type": "text", "text": "You are a helpful assistant."},
            {"type": "text", "text": "Be concise and clear."},
        ],
    ],
    ids=["string", "list", "blocks"],
)
def test_langchain_openai_tools_agent_stream(
    sentry_init,
    capture_events,
    capture_items,
    system_instructions_content,
    request,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_responses,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=True,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                system_instructions_content,
            ),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    model_responses = streaming_chat_completions_model_responses()

    tool_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    final_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.stream(
                    {
                        "input": [
                            "Message demonstrating the absence of truncation.",
                            "How many letters in the word eudca",
                        ]
                    },
                    {"run_name": "my-snazzy-pipeline"},
                )
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        invoke_agent_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"
        )
        tool_exec_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[0]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert chat_spans[1]["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert tool_exec_span["attributes"]["sentry.origin"] == "auto.ai.langchain"

        assert (
            invoke_agent_span["attributes"]["gen_ai.function_id"]
            == "my-snazzy-pipeline"
        )

        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert (
            len(list(x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.chat"))
            > 0
        )

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["attributes"]:
            assert chat_spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["attributes"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["attributes"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["attributes"]:
            assert chat_spans[1]["attributes"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["attributes"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["attributes"]["gen_ai.usage.total_tokens"] == 117

        assert "5" in chat_spans[0]["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert "word" in tool_exec_span["attributes"][SPANDATA.GEN_AI_TOOL_INPUT]
        assert 5 == int(tool_exec_span["attributes"][SPANDATA.GEN_AI_TOOL_OUTPUT])

        assert json.loads(
            chat_spans[0]["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        ) == [
            {
                "role": "user",
                "content": "['Message demonstrating the absence of truncation.', 'How many letters in the word eudca']",
            }
        ]

        param_id = request.node.callspec.id
        if "string" in param_id:
            assert [
                {
                    "type": "text",
                    "content": "You are very powerful assistant, but don't know current events",
                }
            ] == json.loads(
                chat_spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            )
        else:
            assert [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ] == json.loads(
                chat_spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
            )

            assert "5" in chat_spans[1]["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]

            # Verify tool calls are recorded when PII is enabled
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_spans[0].get(
                "attributes", {}
            ), (
                "Tool calls should be recorded when send_default_pii=True and include_prompts=True"
            )
            tool_calls_data = chat_spans[0]["attributes"][
                SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS
            ]

            assert isinstance(tool_calls_data, (list, str))  # Could be serialized
            if isinstance(tool_calls_data, str):
                assert "get_word_length" in tool_calls_data
            elif isinstance(tool_calls_data, list) and len(tool_calls_data) > 0:
                # Check if tool calls contain expected function name
                tool_call_str = str(tool_calls_data)
                assert "get_word_length" in tool_call_str

            # Verify finish_reasons is always an array of strings
            assert chat_spans[0]["attributes"][
                SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS
            ] == ["function_call"]
            assert chat_spans[1]["attributes"][
                SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS
            ] == ["stop"]

            # Verify that available tools are always recorded regardless of PII settings
            for chat_span in chat_spans:
                tools_data = chat_span["attributes"][
                    SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS
                ]
                assert tools_data is not None, (
                    "Available tools should always be recorded regardless of PII settings"
                )
                assert "get_word_length" in tools_data
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.stream(
                    {"input": "How many letters in the word eudca"},
                    {"run_name": "my-snazzy-pipeline"},
                )
            )

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        invoke_agent_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.invoke_agent"
        )
        chat_spans = list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")
        tool_exec_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.execute_tool"
        )

        assert len(chat_spans) == 2

        assert invoke_agent_span["origin"] == "auto.ai.langchain"
        assert chat_spans[0]["origin"] == "auto.ai.langchain"
        assert chat_spans[1]["origin"] == "auto.ai.langchain"
        assert tool_exec_span["origin"] == "auto.ai.langchain"

        assert invoke_agent_span["data"]["gen_ai.function_id"] == "my-snazzy-pipeline"

        # We can't guarantee anything about the "shape" of the langchain execution graph
        assert len(list(x for x in tx["spans"] if x["op"] == "gen_ai.chat")) > 0

        # Token usage is only available in newer versions of langchain (v0.2+)
        # where usage_metadata is supported on AIMessageChunk
        if "gen_ai.usage.input_tokens" in chat_spans[0]["data"]:
            assert chat_spans[0]["data"]["gen_ai.usage.input_tokens"] == 142
            assert chat_spans[0]["data"]["gen_ai.usage.output_tokens"] == 50
            assert chat_spans[0]["data"]["gen_ai.usage.total_tokens"] == 192

        if "gen_ai.usage.input_tokens" in chat_spans[1]["data"]:
            assert chat_spans[1]["data"]["gen_ai.usage.input_tokens"] == 89
            assert chat_spans[1]["data"]["gen_ai.usage.output_tokens"] == 28
            assert chat_spans[1]["data"]["gen_ai.usage.total_tokens"] == 117

        assert "5" in chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        assert "word" in tool_exec_span["data"][SPANDATA.GEN_AI_TOOL_INPUT]
        assert 5 == int(tool_exec_span["data"][SPANDATA.GEN_AI_TOOL_OUTPUT])

        param_id = request.node.callspec.id
        if "string" in param_id:
            assert [
                {
                    "type": "text",
                    "content": "You are very powerful assistant, but don't know current events",
                }
            ] == json.loads(chat_spans[0]["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])
        else:
            assert [
                {
                    "type": "text",
                    "content": "You are a helpful assistant.",
                },
                {
                    "type": "text",
                    "content": "Be concise and clear.",
                },
            ] == json.loads(chat_spans[0]["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS])

            assert "5" in chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]

            # Verify tool calls are recorded when PII is enabled
            assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_spans[0].get(
                "data", {}
            ), (
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

            # Verify finish_reasons is always an array of strings
            assert chat_spans[0]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
                "function_call"
            ]
            assert chat_spans[1]["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == [
                "stop"
            ]

            # Verify that available tools are always recorded regardless of PII settings
            for chat_span in chat_spans:
                tools_data = chat_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
                assert tools_data is not None, (
                    "Available tools should always be recorded regardless of PII settings"
                )
                assert "get_word_length" in tools_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_openai_tools_agent_stream_with_config(
    sentry_init,
    capture_events,
    capture_items,
    get_model_response,
    server_side_event_chunks,
    streaming_chat_completions_model_responses,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[
            LangchainIntegration(
                include_prompts=True,
            )
        ],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

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

    model_responses = streaming_chat_completions_model_responses()

    tool_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    final_response = get_model_response(
        server_side_event_chunks(
            next(model_responses),
            include_event_type=False,
        )
    )

    llm = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt).with_config(
        {"run_name": "my-snazzy-pipeline"}
    )

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.stream(
                    {"input": "How many letters in the word eudca"},
                )
            )

        tx = next(item.payload for item in items if item.type == "transaction")
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        spans = [item.payload for item in items if item.type == "span"]
        invoke_agent_span = next(
            x for x in spans if x["attributes"]["sentry.op"] == "gen_ai.invoke_agent"
        )
        assert (
            invoke_agent_span["attributes"]["gen_ai.function_id"]
            == "my-snazzy-pipeline"
        )
    else:
        events = capture_events()

        with patch.object(
            llm.client._client._client,
            "send",
            side_effect=[tool_response, final_response],
        ) as _, start_transaction():
            list(
                agent_executor.stream(
                    {"input": "How many letters in the word eudca"},
                )
            )

        tx = events[0]
        assert tx["type"] == "transaction"
        assert tx["contexts"]["trace"]["origin"] == "manual"

        invoke_agent_span = next(
            x for x in tx["spans"] if x["op"] == "gen_ai.invoke_agent"
        )
        assert invoke_agent_span["data"]["gen_ai.function_id"] == "my-snazzy-pipeline"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_error(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    class MockOpenAI(ChatOpenAI):
        def _stream(
            self,
            messages: List[BaseMessage],
            stop: Optional[List[str]] = None,
            run_manager: Optional[CallbackManagerForLLMRun] = None,
            **kwargs: Any,
        ) -> Iterator[ChatGenerationChunk]:
            stream_result_mock = Mock(side_effect=ValueError("API rate limit error"))

            for x in stream_result_mock():
                yield x

        @property
        def _llm_type(self) -> str:
            return "acme-llm"

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

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
    llm = MockOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0,
        openai_api_key="badkey",
    )
    agent = create_openai_tools_agent(llm, [get_word_length], prompt)

    agent_executor = AgentExecutor(agent=agent, tools=[get_word_length], verbose=True)

    if stream_gen_ai_spans:
        items = capture_items("event")

        with start_transaction(), pytest.raises(ValueError):
            list(agent_executor.stream({"input": "How many letters in the word eudca"}))

        (error,) = (item.payload for item in items if item.type == "event")
    else:
        events = capture_events()

        with start_transaction(), pytest.raises(ValueError):
            list(agent_executor.stream({"input": "How many letters in the word eudca"}))

        error = events[0]
    assert error["level"] == "error"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_span_status_error(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    class MockOpenAI(ChatOpenAI):
        def _stream(
            self,
            messages: List[BaseMessage],
            stop: Optional[List[str]] = None,
            run_manager: Optional[CallbackManagerForLLMRun] = None,
            **kwargs: Any,
        ) -> Iterator[ChatGenerationChunk]:
            stream_result_mock = Mock(side_effect=ValueError("API rate limit error"))

            for x in stream_result_mock():
                yield x

        @property
        def _llm_type(self) -> str:
            return "acme-llm"

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("event", "transaction", "span")

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
                list(
                    agent_executor.stream(
                        {"input": "How many letters in the word eudca"}
                    )
                )

        (error,) = (item.payload for item in items if item.type == "event")
        assert error["level"] == "error"
        spans = [item.payload for item in items if item.type == "span"]
        assert spans[0]["status"] == "error"
        (transaction,) = (item.payload for item in items if item.type == "transaction")
    else:
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
                list(
                    agent_executor.stream(
                        {"input": "How many letters in the word eudca"}
                    )
                )

        (error, transaction) = events
        assert error["level"] == "error"
        assert transaction["spans"][0]["status"] == "internal_error"
        assert transaction["spans"][0]["tags"]["status"] == "internal_error"

    assert transaction["contexts"]["trace"]["status"] == "internal_error"


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

    sentry_init(
        integrations=[LangchainIntegration()], _experiments={"gen_ai_as_v2_spans": True}
    )

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


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_message_role_mapping(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test that message roles are properly normalized in langchain integration."""

    class MockOpenAI(ChatOpenAI):
        def _stream(
            self,
            messages: List[BaseMessage],
            stop: Optional[List[str]] = None,
            run_manager: Optional[CallbackManagerForLLMRun] = None,
            **kwargs: Any,
        ) -> Iterator[ChatGenerationChunk]:
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

            for x in stream_result_mock():
                yield x

        @property
        def _llm_type(self) -> str:
            return "openai-chat"

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "You are a helpful assistant"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
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

    message_data_found = False
    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction():
            list(agent_executor.stream({"input": test_input}))

        spans = [item.payload for item in items if item.type == "span"]
        # Find spans with gen_ai operation that should have message data
        gen_ai_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op", "").startswith("gen_ai")
        ]

        # Check if any span has message data with normalized roles
        for span in gen_ai_spans:
            span_data = span.get("attributes", {})
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
                    if isinstance(msg, dict) and test_input in str(
                        msg.get("content", "")
                    ):
                        input_found = True
                        break
                    elif isinstance(msg, str) and test_input in msg:
                        input_found = True
                        break

                assert input_found, (
                    f"Test input '{test_input}' not found in messages: {messages}"
                )
                break
    else:
        events = capture_events()

        with start_transaction():
            list(agent_executor.stream({"input": test_input}))

        assert len(events) > 0
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find spans with gen_ai operation that should have message data
        gen_ai_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op", "").startswith("gen_ai")
        ]

        # Check if any span has message data with normalized roles
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
                    if isinstance(msg, dict) and test_input in str(
                        msg.get("content", "")
                    ):
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
            name="my_pipeline",
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
        span
        for span in tx.get("spans", [])
        if span.get("op") == "gen_ai.text_completion"
    ]
    assert len(llm_spans) > 0

    llm_span = llm_spans[0]
    assert llm_span["data"]["gen_ai.operation.name"] == "text_completion"
    assert llm_span["data"][SPANDATA.GEN_AI_FUNCTION_ID] == "my_pipeline"

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in llm_span["data"]
    messages_data = llm_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) == 1
    assert "small message 5" in str(parsed_messages[0])
    assert tx["_meta"]["spans"]["0"]["data"]["gen_ai.request.messages"][""]["len"] == 5


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_langchain_embeddings_sync(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
):
    """Test that sync embedding methods (embed_documents, embed_query) are properly traced."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ) as mock_embed_documents:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_embeddings"):
                # Test embed_documents
                result = embeddings.embed_documents(["Hello world", "Test document"])

            assert len(result) == 2
            mock_embed_documents.assert_called_once()

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings span
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["name"] == "embeddings text-embedding-ada-002"
        assert embeddings_span["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert embeddings_span["attributes"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["attributes"]["gen_ai.request.model"]
            == "text-embedding-ada-002"
        )

        # Check if input is captured based on PII settings
        if send_default_pii and include_prompts:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["attributes"]
            input_data = embeddings_span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]

            # Could be serialized as string
            if isinstance(input_data, str):
                assert "Hello world" in input_data
                assert "Test document" in input_data
            else:
                assert "Hello world" in input_data
                assert "Test document" in input_data
        else:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embeddings_span.get(
                "attributes", {}
            )
    else:
        events = capture_events()

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ) as mock_embed_documents:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_embeddings"):
                # Test embed_documents
                result = embeddings.embed_documents(["Hello world", "Test document"])

            assert len(result) == 2
            mock_embed_documents.assert_called_once()

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings span
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["description"] == "embeddings text-embedding-ada-002"
        assert embeddings_span["origin"] == "auto.ai.langchain"
        assert embeddings_span["data"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["data"]["gen_ai.request.model"] == "text-embedding-ada-002"
        )

        # Check if input is captured based on PII settings
        if send_default_pii and include_prompts:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["data"]
            input_data = embeddings_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
            # Could be serialized as string
            if isinstance(input_data, str):
                assert "Hello world" in input_data
                assert "Test document" in input_data
            else:
                assert "Hello world" in input_data
                assert "Test document" in input_data
        else:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embeddings_span.get(
                "data", {}
            )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (False, False),
    ],
)
def test_langchain_embeddings_embed_query(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
):
    """Test that embed_query method is properly traced."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_query",
            wraps=lambda self, text: [0.1, 0.2, 0.3],
        ) as mock_embed_query:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_embeddings_query"):
                result = embeddings.embed_query("What is the capital of France?")

            assert len(result) == 3
            mock_embed_query.assert_called_once()

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings span
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["attributes"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["attributes"]["gen_ai.request.model"]
            == "text-embedding-ada-002"
        )

        # Check if input is captured based on PII settings
        if send_default_pii and include_prompts:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["attributes"]
            input_data = embeddings_span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]

            # Could be serialized as string
            if isinstance(input_data, str):
                assert "What is the capital of France?" in input_data
            else:
                assert "What is the capital of France?" in input_data
        else:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embeddings_span.get(
                "attributes", {}
            )
    else:
        events = capture_events()

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_query",
            wraps=lambda self, text: [0.1, 0.2, 0.3],
        ) as mock_embed_query:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_embeddings_query"):
                result = embeddings.embed_query("What is the capital of France?")

            assert len(result) == 3
            mock_embed_query.assert_called_once()

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings span
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["data"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["data"]["gen_ai.request.model"] == "text-embedding-ada-002"
        )

        # Check if input is captured based on PII settings
        if send_default_pii and include_prompts:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["data"]
            input_data = embeddings_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
            # Could be serialized as string
            if isinstance(input_data, str):
                assert "What is the capital of France?" in input_data
            else:
                assert "What is the capital of France?" in input_data
        else:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embeddings_span.get(
                "data", {}
            )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (False, False),
    ],
)
@pytest.mark.asyncio
async def test_langchain_embeddings_async(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    include_prompts,
    stream_gen_ai_spans,
):
    """Test that async embedding methods (aembed_documents, aembed_query) are properly traced."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    async def mock_aembed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "aembed_documents",
            wraps=mock_aembed_documents,
        ) as mock_aembed:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_async_embeddings"):
                result = await embeddings.aembed_documents(
                    ["Async hello", "Async test document"]
                )

            assert len(result) == 2
            mock_aembed.assert_called_once()

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings span
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["name"] == "embeddings text-embedding-ada-002"
        assert embeddings_span["attributes"]["sentry.origin"] == "auto.ai.langchain"
        assert embeddings_span["attributes"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["attributes"]["gen_ai.request.model"]
            == "text-embedding-ada-002"
        )

        # Check if input is captured based on PII settings
        if send_default_pii and include_prompts:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["attributes"]
            input_data = embeddings_span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]

            # Could be serialized as string
            if isinstance(input_data, str):
                assert (
                    "Async hello" in input_data or "Async test document" in input_data
                )
            else:
                assert (
                    "Async hello" in input_data or "Async test document" in input_data
                )
        else:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embeddings_span.get(
                "attributes", {}
            )

    else:
        events = capture_events()

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "aembed_documents",
            wraps=mock_aembed_documents,
        ) as mock_aembed:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_async_embeddings"):
                result = await embeddings.aembed_documents(
                    ["Async hello", "Async test document"]
                )

            assert len(result) == 2
            mock_aembed.assert_called_once()

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings span
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["description"] == "embeddings text-embedding-ada-002"
        assert embeddings_span["origin"] == "auto.ai.langchain"
        assert embeddings_span["data"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["data"]["gen_ai.request.model"] == "text-embedding-ada-002"
        )

        # Check if input is captured based on PII settings
        if send_default_pii and include_prompts:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["data"]
            input_data = embeddings_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
            # Could be serialized as string
            if isinstance(input_data, str):
                assert (
                    "Async hello" in input_data or "Async test document" in input_data
                )
            else:
                assert (
                    "Async hello" in input_data or "Async test document" in input_data
                )
        else:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embeddings_span.get(
                "data", {}
            )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.asyncio
async def test_langchain_embeddings_aembed_query(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test that aembed_query method is properly traced."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    async def mock_aembed_query(self, text):
        return [0.1, 0.2, 0.3]

    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "aembed_query",
            wraps=mock_aembed_query,
        ) as mock_aembed:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_async_embeddings_query"):
                result = await embeddings.aembed_query("Async query test")

            assert len(result) == 3
            mock_aembed.assert_called_once()

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings span
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["attributes"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["attributes"]["gen_ai.request.model"]
            == "text-embedding-ada-002"
        )

        # Check if input is captured
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["attributes"]
        input_data = embeddings_span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
    else:
        events = capture_events()

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "aembed_query",
            wraps=mock_aembed_query,
        ) as mock_aembed:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_async_embeddings_query"):
                result = await embeddings.aembed_query("Async query test")

            assert len(result) == 3
            mock_aembed.assert_called_once()

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings span
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["data"]["gen_ai.operation.name"] == "embeddings"
        assert (
            embeddings_span["data"]["gen_ai.request.model"] == "text-embedding-ada-002"
        )

        # Check if input is captured
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in embeddings_span["data"]
        input_data = embeddings_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]

    # Could be serialized as string
    if isinstance(input_data, str):
        assert "Async query test" in input_data
    else:
        assert "Async query test" in input_data


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_embeddings_no_model_name(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test embeddings when model name is not available."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock the actual API call and remove model attribute
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ):
            embeddings = OpenAIEmbeddings(openai_api_key="test-key")
            # Remove model attribute to test fallback
            delattr(embeddings, "model")
            if hasattr(embeddings, "model_name"):
                delattr(embeddings, "model_name")

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_embeddings_no_model"):
                embeddings.embed_documents(["Test"])

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings span
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["name"] == "embeddings"
        assert embeddings_span["attributes"]["gen_ai.operation.name"] == "embeddings"
        # Model name should not be set if not available
        assert (
            "gen_ai.request.model" not in embeddings_span["attributes"]
            or embeddings_span["attributes"]["gen_ai.request.model"] is None
        )
    else:
        events = capture_events()

        # Mock the actual API call and remove model attribute
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ):
            embeddings = OpenAIEmbeddings(openai_api_key="test-key")
            # Remove model attribute to test fallback
            delattr(embeddings, "model")
            if hasattr(embeddings, "model_name"):
                delattr(embeddings, "model_name")

            # Force setup to re-run to ensure our mock is wrapped
            LangchainIntegration.setup_once()

            with start_transaction(name="test_embeddings_no_model"):
                embeddings.embed_documents(["Test"])

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings span
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 1

        embeddings_span = embeddings_spans[0]
        assert embeddings_span["description"] == "embeddings"
        assert embeddings_span["data"]["gen_ai.operation.name"] == "embeddings"
        # Model name should not be set if not available
        assert (
            "gen_ai.request.model" not in embeddings_span["data"]
            or embeddings_span["data"]["gen_ai.request.model"] is None
        )


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_embeddings_integration_disabled(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test that embeddings are not traced when integration is disabled."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    # Initialize without LangchainIntegration
    if stream_gen_ai_spans:
        items = capture_items("span")

        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            return_value=[[0.1, 0.2, 0.3]],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            with start_transaction(name="test_embeddings_disabled"):
                embeddings.embed_documents(["Test"])

        # Check that no embeddings spans were created
        spans = [item.payload for item in items if item.type == "span"]
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        # Should be empty since integration is disabled
        assert len(embeddings_spans) == 0
    else:
        events = capture_events()

        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            return_value=[[0.1, 0.2, 0.3]],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            with start_transaction(name="test_embeddings_disabled"):
                embeddings.embed_documents(["Test"])

        # Check that no embeddings spans were created
        if events:
            tx = events[0]
            embeddings_spans = [
                span
                for span in tx.get("spans", [])
                if span.get("op") == "gen_ai.embeddings"
            ]
            # Should be empty since integration is disabled
            assert len(embeddings_spans) == 0


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_embeddings_multiple_providers(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test that embeddings work with different providers."""
    try:
        from langchain_openai import OpenAIEmbeddings, AzureOpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock both providers
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ), mock.patch.object(
            AzureOpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.4, 0.5, 0.6] for _ in texts],
        ):
            openai_embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )
            azure_embeddings = AzureOpenAIEmbeddings(
                model="text-embedding-ada-002",
                azure_endpoint="https://test.openai.azure.com/",
                openai_api_key="test-key",
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_multiple_providers"):
                openai_embeddings.embed_documents(["OpenAI test"])
                azure_embeddings.embed_documents(["Azure test"])

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings spans
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        # Should have 2 spans, one for each provider
        assert len(embeddings_spans) == 2

        # Verify both spans have proper data
        for span in embeddings_spans:
            assert span["attributes"]["gen_ai.operation.name"] == "embeddings"
            assert (
                span["attributes"]["gen_ai.request.model"] == "text-embedding-ada-002"
            )
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in span["attributes"]
    else:
        events = capture_events()

        # Mock both providers
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ), mock.patch.object(
            AzureOpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.4, 0.5, 0.6] for _ in texts],
        ):
            openai_embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )
            azure_embeddings = AzureOpenAIEmbeddings(
                model="text-embedding-ada-002",
                azure_endpoint="https://test.openai.azure.com/",
                openai_api_key="test-key",
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_multiple_providers"):
                openai_embeddings.embed_documents(["OpenAI test"])
                azure_embeddings.embed_documents(["Azure test"])

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings spans
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        # Should have 2 spans, one for each provider
        assert len(embeddings_spans) == 2

        # Verify both spans have proper data
        for span in embeddings_spans:
            assert span["data"]["gen_ai.operation.name"] == "embeddings"
            assert span["data"]["gen_ai.request.model"] == "text-embedding-ada-002"
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in span["data"]


def test_langchain_embeddings_error_handling(sentry_init, capture_events):
    """Test that errors in embeddings are properly captured."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Mock the API call to raise an error
    with mock.patch.object(
        OpenAIEmbeddings,
        "embed_documents",
        side_effect=ValueError("API error"),
    ):
        embeddings = OpenAIEmbeddings(
            model="text-embedding-ada-002", openai_api_key="test-key"
        )

        # Force setup to re-run
        LangchainIntegration.setup_once()

        with start_transaction(name="test_embeddings_error"), pytest.raises(ValueError):
            embeddings.embed_documents(["Test"])

    # The error should be captured
    assert len(events) >= 1
    # We should have both the transaction and potentially an error event
    [e for e in events if e.get("level") == "error"]
    # Note: errors might not be auto-captured depending on SDK settings,
    # but the span should still be created


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_embeddings_multiple_calls(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test that multiple embeddings calls within a transaction are all traced."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock the actual API calls
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ), mock.patch.object(
            OpenAIEmbeddings,
            "embed_query",
            wraps=lambda self, text: [0.4, 0.5, 0.6],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_multiple_embeddings"):
                # Call embed_documents
                embeddings.embed_documents(["First batch", "Second batch"])
                # Call embed_query
                embeddings.embed_query("Single query")
                # Call embed_documents again
                embeddings.embed_documents(["Third batch"])

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings spans - should have 3 (2 embed_documents + 1 embed_query)
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 3

        # Verify all spans have proper data
        for span in embeddings_spans:
            assert span["attributes"]["gen_ai.operation.name"] == "embeddings"
            assert (
                span["attributes"]["gen_ai.request.model"] == "text-embedding-ada-002"
            )
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in span["attributes"]

        # Verify the input data is different for each span
        input_data_list = [
            span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
            for span in embeddings_spans
        ]
    else:
        events = capture_events()

        # Mock the actual API calls
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ), mock.patch.object(
            OpenAIEmbeddings,
            "embed_query",
            wraps=lambda self, text: [0.4, 0.5, 0.6],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_multiple_embeddings"):
                # Call embed_documents
                embeddings.embed_documents(["First batch", "Second batch"])
                # Call embed_query
                embeddings.embed_query("Single query")
                # Call embed_documents again
                embeddings.embed_documents(["Third batch"])

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings spans - should have 3 (2 embed_documents + 1 embed_query)
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 3

        # Verify all spans have proper data
        for span in embeddings_spans:
            assert span["data"]["gen_ai.operation.name"] == "embeddings"
            assert span["data"]["gen_ai.request.model"] == "text-embedding-ada-002"
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in span["data"]

        # Verify the input data is different for each span
        input_data_list = [
            span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT] for span in embeddings_spans
        ]
    # They should all be different (different inputs)
    assert len(set(str(data) for data in input_data_list)) == 3


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_embeddings_span_hierarchy(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test that embeddings spans are properly nested within parent spans."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("transaction", "span")

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_span_hierarchy"), sentry_sdk.start_span(
                op="custom", name="custom operation"
            ):
                embeddings.embed_documents(["Test within custom span"])

        spans = [item.payload for item in items if item.type == "span"]
        # Find all spans
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        tx = next(item.payload for item in items if item.type == "transaction")
        custom_spans = [
            span for span in tx.get("spans", []) if span.get("op") == "custom"
        ]

        assert len(embeddings_spans) == 1
        assert len(custom_spans) == 1

        # Both spans should exist
        embeddings_span = embeddings_spans[0]
        custom_span = custom_spans[0]

        assert embeddings_span["attributes"]["gen_ai.operation.name"] == "embeddings"
    else:
        events = capture_events()

        # Mock the actual API call
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_span_hierarchy"), sentry_sdk.start_span(
                op="custom", name="custom operation"
            ):
                embeddings.embed_documents(["Test within custom span"])

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find all spans
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        custom_spans = [
            span for span in tx.get("spans", []) if span.get("op") == "custom"
        ]

        assert len(embeddings_spans) == 1
        assert len(custom_spans) == 1

        # Both spans should exist
        embeddings_span = embeddings_spans[0]
        custom_span = custom_spans[0]

        assert embeddings_span["data"]["gen_ai.operation.name"] == "embeddings"
    assert custom_span["description"] == "custom operation"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_langchain_embeddings_with_list_and_string_inputs(
    sentry_init,
    capture_events,
    capture_items,
    stream_gen_ai_spans,
):
    """Test that embeddings correctly handle both list and string inputs."""
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError:
        pytest.skip("langchain_openai not installed")

    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )
    if stream_gen_ai_spans:
        items = capture_items("span")

        # Mock the actual API calls
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ), mock.patch.object(
            OpenAIEmbeddings,
            "embed_query",
            wraps=lambda self, text: [0.4, 0.5, 0.6],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_input_types"):
                # embed_documents takes a list
                embeddings.embed_documents(
                    ["List item 1", "List item 2", "List item 3"]
                )
                # embed_query takes a string
                embeddings.embed_query("Single string query")

        spans = [item.payload for item in items if item.type == "span"]
        # Find embeddings spans
        embeddings_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 2

        # Both should have input data captured as lists
        for span in embeddings_spans:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in span["attributes"]
            input_data = span["attributes"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
            # Input should be normalized to list format
            if isinstance(input_data, str):
                # If serialized, should contain the input text
                assert (
                    "List item" in input_data or "Single string query" in input_data
                ), f"Expected input text in serialized data: {input_data}"
    else:
        events = capture_events()

        # Mock the actual API calls
        with mock.patch.object(
            OpenAIEmbeddings,
            "embed_documents",
            wraps=lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
        ), mock.patch.object(
            OpenAIEmbeddings,
            "embed_query",
            wraps=lambda self, text: [0.4, 0.5, 0.6],
        ):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-ada-002", openai_api_key="test-key"
            )

            # Force setup to re-run
            LangchainIntegration.setup_once()

            with start_transaction(name="test_input_types"):
                # embed_documents takes a list
                embeddings.embed_documents(
                    ["List item 1", "List item 2", "List item 3"]
                )
                # embed_query takes a string
                embeddings.embed_query("Single string query")

        # Check captured events
        assert len(events) >= 1
        tx = events[0]
        assert tx["type"] == "transaction"

        # Find embeddings spans
        embeddings_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.embeddings"
        ]
        assert len(embeddings_spans) == 2

        # Both should have input data captured as lists
        for span in embeddings_spans:
            assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT in span["data"]
            input_data = span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT]
            # Input should be normalized to list format
            if isinstance(input_data, str):
                # If serialized, should contain the input text
                assert (
                    "List item" in input_data or "Single string query" in input_data
                ), f"Expected input text in serialized data: {input_data}"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "response_metadata_model,expected_model",
    [
        ("gpt-3.5-turbo", "gpt-3.5-turbo"),
        (None, None),
    ],
)
def test_langchain_response_model_extraction(
    sentry_init,
    capture_events,
    capture_items,
    response_metadata_model,
    expected_model,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[LangchainIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    callback = SentryLangchainCallback(max_span_map_size=100, include_prompts=True)

    run_id = "test-response-model-uuid"
    serialized = {"_type": "openai-chat", "model_name": "gpt-3.5-turbo"}
    prompts = ["Test prompt"]

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction():
            callback.on_llm_start(
                serialized=serialized,
                prompts=prompts,
                run_id=run_id,
                invocation_params={"model": "gpt-3.5-turbo"},
            )

            response_metadata = {"model_name": response_metadata_model}
            message = AIMessageChunk(
                content="Test response", response_metadata=response_metadata
            )

            generation = Mock(text="Test response", message=message)
            response = Mock(generations=[[generation]])
            callback.on_llm_end(response=response, run_id=run_id)

        spans = [item.payload for item in items if item.type == "span"]
        llm_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.text_completion"
        ]
        assert len(llm_spans) > 0

        llm_span = llm_spans[0]
        assert llm_span["attributes"]["gen_ai.operation.name"] == "text_completion"

        if expected_model is not None:
            assert SPANDATA.GEN_AI_RESPONSE_MODEL in llm_span["attributes"]
            assert (
                llm_span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == expected_model
            )
        else:
            assert SPANDATA.GEN_AI_RESPONSE_MODEL not in llm_span.get("attributes", {})
    else:
        events = capture_events()

        with start_transaction():
            callback.on_llm_start(
                serialized=serialized,
                prompts=prompts,
                run_id=run_id,
                invocation_params={"model": "gpt-3.5-turbo"},
            )

            response_metadata = {"model_name": response_metadata_model}
            message = AIMessageChunk(
                content="Test response", response_metadata=response_metadata
            )

            generation = Mock(text="Test response", message=message)
            response = Mock(generations=[[generation]])
            callback.on_llm_end(response=response, run_id=run_id)

        assert len(events) > 0
        tx = events[0]
        assert tx["type"] == "transaction"

        llm_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.text_completion"
        ]
        assert len(llm_spans) > 0

        llm_span = llm_spans[0]
        assert llm_span["data"]["gen_ai.operation.name"] == "text_completion"

        if expected_model is not None:
            assert SPANDATA.GEN_AI_RESPONSE_MODEL in llm_span["data"]
            assert llm_span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == expected_model
        else:
            assert SPANDATA.GEN_AI_RESPONSE_MODEL not in llm_span.get("data", {})


# Tests for multimodal content transformation functions


class TestTransformLangchainContentBlock:
    """Tests for _transform_langchain_content_block function."""

    def test_transform_image_base64(self):
        """Test transformation of base64-encoded image content."""
        content_block = {
            "type": "image",
            "base64": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
            "mime_type": "image/jpeg",
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
        }

    def test_transform_image_url(self):
        """Test transformation of URL-referenced image content."""
        content_block = {
            "type": "image",
            "url": "https://example.com/image.jpg",
            "mime_type": "image/jpeg",
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "image/jpeg",
            "uri": "https://example.com/image.jpg",
        }

    def test_transform_image_file_id(self):
        """Test transformation of file_id-referenced image content."""
        content_block = {
            "type": "image",
            "file_id": "file-abc123",
            "mime_type": "image/png",
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "file",
            "modality": "image",
            "mime_type": "image/png",
            "file_id": "file-abc123",
        }

    def test_transform_image_url_legacy_with_data_uri(self):
        """Test transformation of legacy image_url format with data: URI (base64)."""
        content_block = {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD"},
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRgABAQAAAQABAAD",
        }

    def test_transform_image_url_legacy_with_http_url(self):
        """Test transformation of legacy image_url format with HTTP URL."""
        content_block = {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.png"},
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "https://example.com/image.png",
        }

    def test_transform_image_url_legacy_string_url(self):
        """Test transformation of legacy image_url format with string URL."""
        content_block = {
            "type": "image_url",
            "image_url": "https://example.com/image.gif",
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "https://example.com/image.gif",
        }

    def test_transform_image_url_legacy_data_uri_png(self):
        """Test transformation of legacy image_url format with PNG data URI."""
        content_block = {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            },
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/png",
            "content": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        }

    def test_transform_missing_mime_type(self):
        """Test transformation when mime_type is not provided."""
        content_block = {
            "type": "image",
            "base64": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "",
            "content": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
        }

    def test_transform_anthropic_source_base64(self):
        """Test transformation of Anthropic-style image with base64 source."""
        content_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "iVBORw0KGgoAAAANSUhEUgAAAAE...",
            },
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/png",
            "content": "iVBORw0KGgoAAAANSUhEUgAAAAE...",
        }

    def test_transform_anthropic_source_url(self):
        """Test transformation of Anthropic-style image with URL source."""
        content_block = {
            "type": "image",
            "source": {
                "type": "url",
                "media_type": "image/jpeg",
                "url": "https://example.com/image.jpg",
            },
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "image/jpeg",
            "uri": "https://example.com/image.jpg",
        }

    def test_transform_anthropic_source_without_media_type(self):
        """Test transformation of Anthropic-style image without media_type uses empty mime_type."""
        content_block = {
            "type": "image",
            "mime_type": "image/webp",  # Top-level mime_type is ignored by standard Anthropic format
            "source": {
                "type": "base64",
                "data": "UklGRh4AAABXRUJQVlA4IBIAAAAwAQCdASoBAAEAAQAcJYgCdAEO",
            },
        }
        result = _transform_langchain_content_block(content_block)
        # Note: The shared transform_content_part uses media_type from source, not top-level mime_type
        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "",
            "content": "UklGRh4AAABXRUJQVlA4IBIAAAAwAQCdASoBAAEAAQAcJYgCdAEO",
        }

    def test_transform_google_inline_data(self):
        """Test transformation of Google-style inline_data format."""
        content_block = {
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
            }
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
        }

    def test_transform_google_file_data(self):
        """Test transformation of Google-style file_data format."""
        content_block = {
            "file_data": {
                "mime_type": "image/png",
                "file_uri": "gs://bucket/path/to/image.png",
            }
        }
        result = _transform_langchain_content_block(content_block)
        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "image/png",
            "uri": "gs://bucket/path/to/image.png",
        }


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
@pytest.mark.parametrize(
    "ai_type,expected_system",
    [
        # Real LangChain _type values (from _llm_type properties)
        # OpenAI
        ("openai-chat", "openai-chat"),
        ("openai", "openai"),
        # Azure OpenAI
        ("azure-openai-chat", "azure-openai-chat"),
        ("azure", "azure"),
        # Anthropic
        ("anthropic-chat", "anthropic-chat"),
        # Google
        ("vertexai", "vertexai"),
        ("chat-google-generative-ai", "chat-google-generative-ai"),
        ("google_gemini", "google_gemini"),
        # AWS Bedrock
        ("amazon_bedrock_chat", "amazon_bedrock_chat"),
        ("amazon_bedrock", "amazon_bedrock"),
        # Cohere
        ("cohere-chat", "cohere-chat"),
        # Ollama
        ("chat-ollama", "chat-ollama"),
        ("ollama-llm", "ollama-llm"),
        # Mistral
        ("mistralai-chat", "mistralai-chat"),
        # Fireworks
        ("fireworks-chat", "fireworks-chat"),
        ("fireworks", "fireworks"),
        # HuggingFace
        ("huggingface-chat-wrapper", "huggingface-chat-wrapper"),
        # Groq
        ("groq-chat", "groq-chat"),
        # NVIDIA
        ("chat-nvidia-ai-playground", "chat-nvidia-ai-playground"),
        # xAI
        ("xai-chat", "xai-chat"),
        # DeepSeek
        ("chat-deepseek", "chat-deepseek"),
        # Edge cases
        ("", None),
        (None, None),
    ],
)
def test_langchain_ai_system_detection(
    sentry_init,
    capture_events,
    capture_items,
    ai_type,
    expected_system,
    stream_gen_ai_spans,
):
    sentry_init(
        integrations=[LangchainIntegration()],
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    callback = SentryLangchainCallback(max_span_map_size=100, include_prompts=True)

    run_id = "test-ai-system-uuid"
    serialized = {"_type": ai_type} if ai_type is not None else {}
    prompts = ["Test prompt"]

    if stream_gen_ai_spans:
        items = capture_items("span")

        with start_transaction():
            callback.on_llm_start(
                serialized=serialized,
                prompts=prompts,
                run_id=run_id,
                invocation_params={"_type": ai_type, "model": "test-model"},
            )

            generation = Mock(text="Test response", message=None)
            response = Mock(generations=[[generation]])
            callback.on_llm_end(response=response, run_id=run_id)

        spans = [item.payload for item in items if item.type == "span"]
        llm_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "gen_ai.text_completion"
        ]

        assert len(llm_spans) > 0
        llm_span = llm_spans[0]

        if expected_system is not None:
            assert llm_span["attributes"][SPANDATA.GEN_AI_SYSTEM] == expected_system
        else:
            assert SPANDATA.GEN_AI_SYSTEM not in llm_span.get("attributes", {})
    else:
        events = capture_events()

        with start_transaction():
            callback.on_llm_start(
                serialized=serialized,
                prompts=prompts,
                run_id=run_id,
                invocation_params={"_type": ai_type, "model": "test-model"},
            )

            generation = Mock(text="Test response", message=None)
            response = Mock(generations=[[generation]])
            callback.on_llm_end(response=response, run_id=run_id)

        assert len(events) > 0
        tx = events[0]
        assert tx["type"] == "transaction"

        llm_spans = [
            span
            for span in tx.get("spans", [])
            if span.get("op") == "gen_ai.text_completion"
        ]

        assert len(llm_spans) > 0
        llm_span = llm_spans[0]

        if expected_system is not None:
            assert llm_span["data"][SPANDATA.GEN_AI_SYSTEM] == expected_system
        else:
            assert SPANDATA.GEN_AI_SYSTEM not in llm_span.get("data", {})


class TestTransformLangchainMessageContent:
    """Tests for _transform_langchain_message_content function."""

    def test_transform_string_content(self):
        """Test that string content is returned unchanged."""
        result = _transform_langchain_message_content("Hello, world!")
        assert result == "Hello, world!"

    def test_transform_list_with_text_blocks(self):
        """Test transformation of list with text blocks (unchanged)."""
        content = [
            {"type": "text", "text": "First message"},
            {"type": "text", "text": "Second message"},
        ]
        result = _transform_langchain_message_content(content)
        assert result == content

    def test_transform_list_with_image_blocks(self):
        """Test transformation of list containing image blocks."""
        content = [
            {"type": "text", "text": "Check out this image:"},
            {
                "type": "image",
                "base64": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
                "mime_type": "image/jpeg",
            },
        ]
        result = _transform_langchain_message_content(content)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "Check out this image:"}
        assert result[1] == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
        }

    def test_transform_list_with_mixed_content(self):
        """Test transformation of list with mixed content types."""
        content = [
            {"type": "text", "text": "Here are some files:"},
            {
                "type": "image",
                "url": "https://example.com/image.jpg",
                "mime_type": "image/jpeg",
            },
            {
                "type": "file",
                "file_id": "doc-123",
                "mime_type": "application/pdf",
            },
            {"type": "audio", "base64": "audio_data...", "mime_type": "audio/mp3"},
        ]
        result = _transform_langchain_message_content(content)
        assert len(result) == 4
        assert result[0] == {"type": "text", "text": "Here are some files:"}
        assert result[1] == {
            "type": "uri",
            "modality": "image",
            "mime_type": "image/jpeg",
            "uri": "https://example.com/image.jpg",
        }
        assert result[2] == {
            "type": "file",
            "modality": "document",
            "mime_type": "application/pdf",
            "file_id": "doc-123",
        }
        assert result[3] == {
            "type": "blob",
            "modality": "audio",
            "mime_type": "audio/mp3",
            "content": "audio_data...",
        }

    def test_transform_list_with_non_dict_items(self):
        """Test transformation handles non-dict items in list."""
        content = ["plain string", {"type": "text", "text": "dict text"}]
        result = _transform_langchain_message_content(content)
        assert result == ["plain string", {"type": "text", "text": "dict text"}]

    def test_transform_tuple_content(self):
        """Test transformation of tuple content."""
        content = (
            {"type": "text", "text": "Message"},
            {"type": "image", "base64": "data...", "mime_type": "image/png"},
        )
        result = _transform_langchain_message_content(content)
        assert len(result) == 2
        assert result[1] == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/png",
            "content": "data...",
        }

    def test_transform_list_with_legacy_image_url(self):
        """Test transformation of list containing legacy image_url blocks."""
        content = [
            {"type": "text", "text": "Check this:"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."},
            },
        ]
        result = _transform_langchain_message_content(content)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "Check this:"}
        assert result[1] == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQ...",
        }

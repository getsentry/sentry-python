import json
from unittest import mock  # python 3.3 and above

import httpx
import pytest
from cohere import ChatMessage, Client
from httpx import Client as HTTPXClient

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.cohere import CohereIntegration


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_nonstreaming_chat(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "text": "the model response",
                "meta": {
                    "billed_units": {
                        "output_tokens": 10,
                        "input_tokens": 20,
                    }
                },
            },
        )
    )
    items = capture_items("span")

    response = client.chat(
        model="some-model",
        chat_history=[ChatMessage(role="SYSTEM", message="some context")],
        message="hello",
    ).text

    assert response == "the model response"
    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload

    assert span["attributes"]["sentry.op"] == "ai.chat_completions.create.cohere"
    assert span["attributes"][SPANDATA.AI_MODEL_ID] == "some-model"

    if send_default_pii and include_prompts:
        assert (
            '{"role": "system", "content": "some context"}'
            in span["attributes"][SPANDATA.AI_INPUT_MESSAGES]
        )
        assert (
            '{"role": "user", "content": "hello"}'
            in span["attributes"][SPANDATA.AI_INPUT_MESSAGES]
        )
        assert "the model response" in span["attributes"][SPANDATA.AI_RESPONSES]
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["attributes"]
        assert SPANDATA.AI_RESPONSES not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


# noinspection PyTypeChecker
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_streaming_chat(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    client = Client(api_key="z")
    HTTPXClient.send = mock.Mock(
        return_value=httpx.Response(
            200,
            content="\n".join(
                [
                    json.dumps({"event_type": "text-generation", "text": "the model "}),
                    json.dumps({"event_type": "text-generation", "text": "response"}),
                    json.dumps(
                        {
                            "event_type": "stream-end",
                            "finish_reason": "COMPLETE",
                            "response": {
                                "text": "the model response",
                                "meta": {
                                    "billed_units": {
                                        "output_tokens": 10,
                                        "input_tokens": 20,
                                    }
                                },
                            },
                        }
                    ),
                ]
            ),
        )
    )
    items = capture_items("span")

    responses = list(
        client.chat_stream(
            model="some-model",
            chat_history=[ChatMessage(role="SYSTEM", message="some context")],
            message="hello",
        )
    )
    response_string = responses[-1].response.text

    assert response_string == "the model response"
    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload

    assert span["attributes"]["sentry.op"] == "ai.chat_completions.create.cohere"
    assert span["attributes"][SPANDATA.AI_MODEL_ID] == "some-model"

    if send_default_pii and include_prompts:
        assert (
            '{"role": "system", "content": "some context"}'
            in span["attributes"][SPANDATA.AI_INPUT_MESSAGES]
        )
        assert (
            '{"role": "user", "content": "hello"}'
            in span["attributes"][SPANDATA.AI_INPUT_MESSAGES]
        )
        assert "the model response" in span["attributes"][SPANDATA.AI_RESPONSES]
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["attributes"]
        assert SPANDATA.AI_RESPONSES not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.output_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 30


def test_bad_chat(sentry_init, capture_items):
    sentry_init(
        integrations=[CohereIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("event", "span")

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        side_effect=httpx.HTTPError("API rate limit reached")
    )
    with pytest.raises(httpx.HTTPError):
        client.chat(model="some-model", message="hello")

    (event,) = (item.payload for item in items if item.type == "event")
    assert event["level"] == "error"

    sentry_sdk.flush()
    (span,) = (item.payload for item in items if item.type == "span")
    assert span["status"] == "error"


def test_span_status_error(sentry_init, capture_items):
    sentry_init(
        integrations=[CohereIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        side_effect=httpx.HTTPError("API rate limit reached")
    )
    with pytest.raises(httpx.HTTPError):
        client.chat(model="some-model", message="hello")

    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload
    assert span["status"] == "error"


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_embed(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "response_type": "embeddings_floats",
                "id": "1",
                "texts": ["hello"],
                "embeddings": [[1.0, 2.0, 3.0]],
                "meta": {
                    "billed_units": {
                        "input_tokens": 10,
                    }
                },
            },
        )
    )
    items = capture_items("span")

    response = client.embed(texts=["hello"], model="text-embedding-3-large")

    assert len(response.embeddings[0]) == 3
    sentry_sdk.flush()

    assert len(items) == 1
    span = items[0].payload

    assert span["attributes"]["sentry.op"] == "ai.embeddings.create.cohere"
    if send_default_pii and include_prompts:
        assert "hello" in span["attributes"][SPANDATA.AI_INPUT_MESSAGES]
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["attributes"]

    assert span["attributes"]["gen_ai.usage.input_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 10


def test_span_origin_chat(sentry_init, capture_items):
    sentry_init(
        integrations=[CohereIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "text": "the model response",
                "meta": {
                    "billed_units": {
                        "output_tokens": 10,
                        "input_tokens": 20,
                    }
                },
            },
        )
    )

    client.chat(
        model="some-model",
        chat_history=[ChatMessage(role="SYSTEM", message="some context")],
        message="hello",
    ).text

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    assert span["attributes"]["sentry.origin"] == "auto.ai.cohere"


def test_span_origin_embed(sentry_init, capture_items):
    sentry_init(
        integrations=[CohereIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "response_type": "embeddings_floats",
                "id": "1",
                "texts": ["hello"],
                "embeddings": [[1.0, 2.0, 3.0]],
                "meta": {
                    "billed_units": {
                        "input_tokens": 10,
                    }
                },
            },
        )
    )

    client.embed(texts=["hello"], model="text-embedding-3-large")

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    assert span["attributes"]["sentry.origin"] == "auto.ai.cohere"


# data_collection config, send_default_pii, include_prompts, expect_inputs, expect_outputs
DATA_COLLECTION_CASES = [
    pytest.param(
        {"gen_ai": {"inputs": True, "outputs": True}},
        False,
        False,
        True,
        True,
        id="gen-ai-inputs-and-outputs-enabled-override-legacy-off",
    ),
    pytest.param(
        {"gen_ai": {"inputs": False, "outputs": False}},
        True,
        True,
        False,
        False,
        id="gen-ai-inputs-and-outputs-disabled-override-legacy-on",
    ),
    pytest.param(
        {"gen_ai": {"inputs": True, "outputs": False}},
        False,
        False,
        True,
        False,
        id="gen-ai-inputs-enabled-outputs-disabled",
    ),
    pytest.param(
        {"gen_ai": {"inputs": False, "outputs": True}},
        False,
        False,
        False,
        True,
        id="gen-ai-outputs-enabled-inputs-disabled",
    ),
    pytest.param(
        {"gen_ai": {}},
        False,
        False,
        True,
        True,
        id="gen-ai-inputs-and-outputs-omitted-default-to-enabled",
    ),
    pytest.param(
        None,
        True,
        True,
        True,
        True,
        id="no-gen-ai-config-legacy-pii-and-include-prompts-enabled",
    ),
    pytest.param(
        None,
        False,
        True,
        False,
        False,
        id="no-gen-ai-config-legacy-pii-disabled",
    ),
]


def _init_with_data_collection(
    sentry_init,
    data_collection,
    send_default_pii,
    include_prompts,
):
    kwargs = dict(
        integrations=[CohereIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        kwargs["_experiments"] = {"data_collection": data_collection}

    sentry_init(**kwargs)


@pytest.mark.parametrize(
    "data_collection, send_default_pii, include_prompts, expect_inputs, expect_outputs",
    DATA_COLLECTION_CASES,
)
def test_nonstreaming_chat_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    expect_inputs,
    expect_outputs,
):
    _init_with_data_collection(
        sentry_init,
        data_collection,
        send_default_pii,
        include_prompts,
    )

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "text": "the model response",
                "generation_id": "gen-1",
                "citations": [
                    {
                        "start": 0,
                        "end": 3,
                        "text": "the",
                        "document_ids": ["doc-1"],
                    }
                ],
                "meta": {
                    "billed_units": {
                        "output_tokens": 10,
                        "input_tokens": 20,
                    }
                },
            },
        )
    )
    items = capture_items("span")

    client.chat(
        model="some-model",
        chat_history=[ChatMessage(role="SYSTEM", message="some context")],
        message="hello",
        preamble="be concise",
    )
    sentry_sdk.flush()
    assert len(items) == 1
    attributes = items[0].payload["attributes"]

    assert attributes[SPANDATA.AI_MODEL_ID] == "some-model"
    assert attributes["gen_ai.usage.input_tokens"] == 20
    assert attributes["gen_ai.usage.output_tokens"] == 10
    assert attributes["ai.generation_id"] == "gen-1"

    if expect_inputs:
        assert '{"role": "user", "content": "hello"}' in str(
            attributes[SPANDATA.AI_INPUT_MESSAGES]
        )
        assert attributes[SPANDATA.AI_PREAMBLE] == "be concise"
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in attributes
        assert SPANDATA.AI_PREAMBLE not in attributes

    if expect_outputs:
        assert "the model response" in str(attributes[SPANDATA.AI_RESPONSES])
        assert "doc-1" in str(attributes["ai.citations"])
    else:
        assert SPANDATA.AI_RESPONSES not in attributes
        assert "ai.citations" not in attributes


@pytest.mark.parametrize(
    "data_collection, send_default_pii, include_prompts, expect_inputs, expect_outputs",
    DATA_COLLECTION_CASES,
)
def test_streaming_chat_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    expect_inputs,
    expect_outputs,
):
    _init_with_data_collection(
        sentry_init,
        data_collection,
        send_default_pii,
        include_prompts,
    )

    client = Client(api_key="z")
    HTTPXClient.send = mock.Mock(
        return_value=httpx.Response(
            200,
            content="\n".join(
                [
                    json.dumps({"event_type": "text-generation", "text": "the model "}),
                    json.dumps({"event_type": "text-generation", "text": "response"}),
                    json.dumps(
                        {
                            "event_type": "stream-end",
                            "finish_reason": "COMPLETE",
                            "response": {
                                "text": "the model response",
                                "generation_id": "gen-1",
                                "citations": [
                                    {
                                        "start": 0,
                                        "end": 3,
                                        "text": "the",
                                        "document_ids": ["doc-1"],
                                    }
                                ],
                                "meta": {
                                    "billed_units": {
                                        "output_tokens": 10,
                                        "input_tokens": 20,
                                    }
                                },
                            },
                        }
                    ),
                ]
            ),
        )
    )
    items = capture_items("span")

    list(
        client.chat_stream(
            model="some-model",
            chat_history=[ChatMessage(role="SYSTEM", message="some context")],
            message="hello",
            preamble="be concise",
        )
    )
    sentry_sdk.flush()
    assert len(items) == 1
    attributes = items[0].payload["attributes"]

    assert attributes[SPANDATA.AI_MODEL_ID] == "some-model"
    assert attributes["gen_ai.usage.input_tokens"] == 20
    assert attributes["gen_ai.usage.output_tokens"] == 10

    if expect_inputs:
        assert '{"role": "user", "content": "hello"}' in str(
            attributes[SPANDATA.AI_INPUT_MESSAGES]
        )
        assert attributes[SPANDATA.AI_PREAMBLE] == "be concise"
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in attributes
        assert SPANDATA.AI_PREAMBLE not in attributes

    if expect_outputs:
        assert "the model response" in str(attributes[SPANDATA.AI_RESPONSES])
        assert "doc-1" in str(attributes["ai.citations"])
    else:
        assert SPANDATA.AI_RESPONSES not in attributes
        assert "ai.citations" not in attributes


@pytest.mark.parametrize(
    "data_collection, send_default_pii, include_prompts, expect_inputs, expect_outputs",
    DATA_COLLECTION_CASES,
)
def test_embed_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    expect_inputs,
    expect_outputs,
):
    _init_with_data_collection(
        sentry_init,
        data_collection,
        send_default_pii,
        include_prompts,
    )

    client = Client(api_key="z")
    HTTPXClient.request = mock.Mock(
        return_value=httpx.Response(
            200,
            json={
                "response_type": "embeddings_floats",
                "id": "1",
                "texts": ["hello"],
                "embeddings": [[1.0, 2.0, 3.0]],
                "meta": {
                    "billed_units": {
                        "input_tokens": 10,
                    }
                },
            },
        )
    )
    items = capture_items("span")

    client.embed(texts=["hello"], model="text-embedding-3-large")
    sentry_sdk.flush()
    assert len(items) == 1
    attributes = items[0].payload["attributes"]

    assert attributes[SPANDATA.AI_MODEL_ID] == "text-embedding-3-large"
    assert attributes["gen_ai.usage.input_tokens"] == 10

    if expect_inputs:
        assert "hello" in str(attributes[SPANDATA.AI_INPUT_MESSAGES])
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in attributes

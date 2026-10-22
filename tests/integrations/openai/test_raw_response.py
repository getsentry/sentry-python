"""`.with_raw_response` and `.with_streaming_response` must fill the span."""

import inspect
from types import SimpleNamespace
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.openai import OpenAIIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration

pytest.importorskip("openai")
httpx2 = pytest.importorskip("httpx2")

from openai import AsyncOpenAI, OpenAI  # noqa: E402
from openai.types import CompletionUsage  # noqa: E402
from openai.types.chat import ChatCompletion, ChatCompletionMessage  # noqa: E402
from openai.types.chat.chat_completion import Choice  # noqa: E402

COMPLETION = ChatCompletion(
    id="chatcmpl-1",
    choices=[
        Choice(
            index=0,
            finish_reason="stop",
            message=ChatCompletionMessage(role="assistant", content="hi there"),
        )
    ],
    created=1,
    model="gpt-4o-mini",
    object="chat.completion",
    usage=CompletionUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10),
)

COMPLETION_JSON = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hi there"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
}

KWARGS = dict(model="gpt-4o-mini", messages=[{"role": "user", "content": "hello"}])


class UnreadResponse:
    """A response wrapper whose body has not been read yet."""

    def __init__(self, parsed, consumed):
        self.http_response = SimpleNamespace(is_stream_consumed=consumed)
        self._parsed = parsed
        self.parse_calls = 0
        self.closed = False

    def parse(self):
        self.parse_calls += 1
        return self._parsed

    def close(self):
        self.closed = True


def _init(sentry_init):
    sentry_init(
        integrations=[OpenAIIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=False,
    )


def _gen_ai_spans(event):
    return [span for span in event["spans"] if span["op"] == OP.GEN_AI_CHAT]


def _assert_has_output(span):
    data = span["data"]
    assert data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 7
    assert data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3
    assert "hi there" in str(data[SPANDATA.GEN_AI_RESPONSE_TEXT])


def test_raw_and_streaming_responses_record_output(sentry_init, capture_events):
    _init(sentry_init)
    events = capture_events()

    client = OpenAI(
        api_key="z",
        http_client=httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=COMPLETION_JSON)
            )
        ),
    )

    with sentry_sdk.start_transaction(name="t"):
        client.chat.completions.with_raw_response.create(**KWARGS).parse()
        with client.chat.completions.with_streaming_response.create(
            **KWARGS
        ) as response:
            response.parse()

    sentry_sdk.flush()
    (event,) = events
    spans = _gen_ai_spans(event)
    assert len(spans) == 2
    for span in spans:
        _assert_has_output(span)


def test_unread_body_is_parsed_by_the_caller(sentry_init, capture_events):
    _init(sentry_init)
    events = capture_events()

    client = OpenAI(api_key="z")
    unread = UnreadResponse(COMPLETION, consumed=False)
    client.chat.completions._post = mock.Mock(return_value=unread)

    with sentry_sdk.start_transaction(name="t"):
        response = client.chat.completions.with_raw_response.create(**KWARGS)
        assert response.parse_calls == 0
        response.parse()
        assert response.parse_calls == 1
        response.close()

    assert response is unread
    assert unread.closed

    sentry_sdk.flush()
    (event,) = events
    (span,) = _gen_ai_spans(event)
    _assert_has_output(span)


@pytest.mark.asyncio
async def test_async_raw_response_records_output(sentry_init, capture_events):
    _init(sentry_init)
    events = capture_events()

    client = AsyncOpenAI(
        api_key="z",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=COMPLETION_JSON)
            )
        ),
    )

    with sentry_sdk.start_transaction(name="t"):
        response = await client.chat.completions.with_raw_response.create(**KWARGS)
        parsed = response.parse()
        if inspect.isawaitable(parsed):
            parsed = await parsed
        assert parsed is not None

    sentry_sdk.flush()
    (event,) = events
    (span,) = _gen_ai_spans(event)
    _assert_has_output(span)

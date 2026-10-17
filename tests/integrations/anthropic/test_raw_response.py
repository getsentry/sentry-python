"""`.with_raw_response` and `.with_streaming_response` must fill the span."""

import inspect
from types import SimpleNamespace
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.anthropic import AnthropicIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from anthropic import Anthropic, AsyncAnthropic  # noqa: E402
from anthropic.types import Message, TextBlock, Usage  # noqa: E402

MESSAGE = Message(
    id="msg_01",
    model="claude-sonnet-4-5",
    role="assistant",
    content=[TextBlock(type="text", text="hi there")],
    type="message",
    stop_reason="end_turn",
    usage=Usage(input_tokens=7, output_tokens=3),
)

MESSAGE_JSON = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-5",
    "content": [{"type": "text", "text": "hi there"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 7, "output_tokens": 3},
}

KWARGS = dict(
    model="claude-sonnet-4-5",
    max_tokens=10,
    messages=[{"role": "user", "content": "hello"}],
)


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
        integrations=[AnthropicIntegration(include_prompts=True)],
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
    assert data[SPANDATA.GEN_AI_RESPONSE_ID] == "msg_01"
    assert data[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["end_turn"]
    assert data[SPANDATA.GEN_AI_RESPONSE_TEXT] == "hi there"
    assert "unknown_response" not in data


def test_raw_and_streaming_responses_record_output(sentry_init, capture_events):
    _init(sentry_init)
    events = capture_events()

    client = Anthropic(
        api_key="z",
        http_client=httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=MESSAGE_JSON)
            )
        ),
    )

    with sentry_sdk.start_transaction(name="t"):
        client.messages.with_raw_response.create(**KWARGS).parse()
        with client.messages.with_streaming_response.create(**KWARGS) as response:
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

    client = Anthropic(api_key="z")
    unread = UnreadResponse(MESSAGE, consumed=False)
    client.messages._post = mock.Mock(return_value=unread)

    with sentry_sdk.start_transaction(name="t"):
        with client.messages.with_streaming_response.create(**KWARGS) as response:
            assert response.parse_calls == 0
            response.parse()
            assert response.parse_calls == 1

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

    client = AsyncAnthropic(
        api_key="z",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=MESSAGE_JSON)
            )
        ),
    )

    with sentry_sdk.start_transaction(name="t"):
        response = await client.messages.with_raw_response.create(**KWARGS)
        parsed = response.parse()
        if inspect.isawaitable(parsed):
            parsed = await parsed
        assert parsed is not None

    sentry_sdk.flush()
    (event,) = events
    (span,) = _gen_ai_spans(event)
    _assert_has_output(span)

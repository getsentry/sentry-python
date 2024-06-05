import pytest
from unittest import mock
from anthropic import Anthropic, Stream, AnthropicError
from anthropic.types import Usage, MessageDeltaUsage, TextDelta
from anthropic.types.message import Message
from anthropic.types.message_delta_event import MessageDeltaEvent
from anthropic.types.message_start_event import MessageStartEvent
from anthropic.types.content_block_start_event import ContentBlockStartEvent
from anthropic.types.content_block_delta_event import ContentBlockDeltaEvent
from anthropic.types.content_block_stop_event import ContentBlockStopEvent

try:
    # 0.27+
    from anthropic.types.raw_message_delta_event import Delta
except ImportError:
    # pre 0.27
    from anthropic.types.message_delta_event import Delta

try:
    from anthropic.types.text_block import TextBlock
except ImportError:
    from anthropic.types.content_block import ContentBlock as TextBlock

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.anthropic import AnthropicIntegration


EXAMPLE_MESSAGE = Message(
    id="id",
    model="model",
    role="assistant",
    content=[TextBlock(type="text", text="Hi, I'm Claude.")],
    type="message",
    usage=Usage(input_tokens=10, output_tokens=20),
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
def test_nonstreaming_create_message(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]

    with start_transaction(name="anthropic"):
        response = client.messages.create(
            max_tokens=1024, messages=messages, model="model"
        )

    assert response == EXAMPLE_MESSAGE
    usage = response.usage

    assert usage.input_tokens == 10
    assert usage.output_tokens == 20

    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert event["transaction"] == "anthropic"

    assert len(event["spans"]) == 1
    (span,) = event["spans"]

    assert span["op"] == OP.ANTHROPIC_MESSAGES_CREATE
    assert span["description"] == "Anthropic messages create"
    assert span["data"][SPANDATA.AI_MODEL_ID] == "model"

    if send_default_pii and include_prompts:
        assert span["data"][SPANDATA.AI_INPUT_MESSAGES] == messages
        assert span["data"][SPANDATA.AI_RESPONSES] == [
            {"type": "text", "text": "Hi, I'm Claude."}
        ]
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["data"]
        assert SPANDATA.AI_RESPONSES not in span["data"]

    assert span["measurements"]["ai_prompt_tokens_used"]["value"] == 10
    assert span["measurements"]["ai_completion_tokens_used"]["value"] == 20
    assert span["measurements"]["ai_total_tokens_used"]["value"] == 30
    assert span["data"]["ai.streaming"] is False


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_streaming_create_message(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    client = Anthropic(api_key="z")
    returned_stream = Stream(cast_to=None, response=None, client=client)
    returned_stream._iterator = [
        MessageStartEvent(
            message=EXAMPLE_MESSAGE,
            type="message_start",
        ),
        ContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text=""),
        ),
        ContentBlockDeltaEvent(
            delta=TextDelta(text="Hi", type="text_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockDeltaEvent(
            delta=TextDelta(text="!", type="text_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockDeltaEvent(
            delta=TextDelta(text=" I'm Claude!", type="text_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockStopEvent(type="content_block_stop", index=0),
        MessageDeltaEvent(
            delta=Delta(),
            usage=MessageDeltaUsage(output_tokens=10),
            type="message_delta",
        ),
    ]

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()
    client.messages._post = mock.Mock(return_value=returned_stream)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]

    with start_transaction(name="anthropic"):
        message = client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        for _ in message:
            pass

    assert message == returned_stream
    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert event["transaction"] == "anthropic"

    assert len(event["spans"]) == 1
    (span,) = event["spans"]

    assert span["op"] == OP.ANTHROPIC_MESSAGES_CREATE
    assert span["description"] == "Anthropic messages create"
    assert span["data"][SPANDATA.AI_MODEL_ID] == "model"

    if send_default_pii and include_prompts:
        assert span["data"][SPANDATA.AI_INPUT_MESSAGES] == messages
        assert span["data"][SPANDATA.AI_RESPONSES] == [
            {"type": "text", "text": "Hi! I'm Claude!"}
        ]

    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["data"]
        assert SPANDATA.AI_RESPONSES not in span["data"]

    assert span["measurements"]["ai_prompt_tokens_used"]["value"] == 10
    assert span["measurements"]["ai_completion_tokens_used"]["value"] == 30
    assert span["measurements"]["ai_total_tokens_used"]["value"] == 40
    assert span["data"]["ai.streaming"] is True


def test_exception_message_create(sentry_init, capture_events):
    sentry_init(integrations=[AnthropicIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(
        side_effect=AnthropicError("API rate limit reached")
    )
    with pytest.raises(AnthropicError):
        client.messages.create(
            model="some-model",
            messages=[{"role": "system", "content": "I'm throwing an exception"}],
            max_tokens=1024,
        )

    (event,) = events
    assert event["level"] == "error"


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[AnthropicIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(
            max_tokens=1024, messages=messages, model="model"
        )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.anthropic"

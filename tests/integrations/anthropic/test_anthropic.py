from unittest import mock

from sentry_sdk.integrations import DidNotEnable

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


import pytest
from anthropic import Anthropic, AnthropicError, AsyncAnthropic, AsyncStream, Stream
from anthropic.types import MessageDeltaUsage, TextDelta, Usage
from anthropic.types.content_block_delta_event import ContentBlockDeltaEvent
from anthropic.types.content_block_start_event import ContentBlockStartEvent
from anthropic.types.content_block_stop_event import ContentBlockStopEvent
from anthropic.types.message import Message
from anthropic.types.message_delta_event import MessageDeltaEvent
from anthropic.types.message_start_event import MessageStartEvent

from sentry_sdk.integrations.anthropic import _add_ai_data_to_span, _collect_ai_data
from sentry_sdk.utils import package_version

try:
    from anthropic.types import InputJSONDelta
except ImportError:
    try:
        from anthropic.types import InputJsonDelta as InputJSONDelta
    except ImportError:
        pass

try:
    # 0.27+
    from anthropic.types.raw_message_delta_event import Delta
    from anthropic.types.tool_use_block import ToolUseBlock
except ImportError:
    # pre 0.27
    from anthropic.types.message_delta_event import Delta

try:
    from anthropic.types.text_block import TextBlock
except ImportError:
    from anthropic.types.content_block import ContentBlock as TextBlock

from sentry_sdk import start_transaction, start_span
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.anthropic import AnthropicIntegration

ANTHROPIC_VERSION = package_version("anthropic")
EXAMPLE_MESSAGE = Message(
    id="id",
    model="model",
    role="assistant",
    content=[TextBlock(type="text", text="Hi, I'm Claude.")],
    type="message",
    usage=Usage(input_tokens=10, output_tokens=20),
)


async def async_iterator(values):
    for value in values:
        yield value


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_nonstreaming_create_message_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()
    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]

    with start_transaction(name="anthropic"):
        response = await client.messages.create(
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
async def test_streaming_create_message_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    client = AsyncAnthropic(api_key="z")
    returned_stream = AsyncStream(cast_to=None, response=None, client=client)
    returned_stream._iterator = async_iterator(
        [
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
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()
    client.messages._post = AsyncMock(return_value=returned_stream)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]

    with start_transaction(name="anthropic"):
        message = await client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        async for _ in message:
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


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="Versions <0.27.0 do not include InputJSONDelta, which was introduced in >=0.27.0 along with a new message delta type for tool calling.",
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
def test_streaming_create_message_with_input_json_delta(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    client = Anthropic(api_key="z")
    returned_stream = Stream(cast_to=None, response=None, client=client)
    returned_stream._iterator = [
        MessageStartEvent(
            message=Message(
                id="msg_0",
                content=[],
                model="claude-3-5-sonnet-20240620",
                role="assistant",
                stop_reason=None,
                stop_sequence=None,
                type="message",
                usage=Usage(input_tokens=366, output_tokens=10),
            ),
            type="message_start",
        ),
        ContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock(
                id="toolu_0", input={}, name="get_weather", type="tool_use"
            ),
        ),
        ContentBlockDeltaEvent(
            delta=InputJSONDelta(partial_json="", type="input_json_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockDeltaEvent(
            delta=InputJSONDelta(partial_json="{'location':", type="input_json_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockDeltaEvent(
            delta=InputJSONDelta(partial_json=" 'S", type="input_json_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockDeltaEvent(
            delta=InputJSONDelta(partial_json="an ", type="input_json_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockDeltaEvent(
            delta=InputJSONDelta(partial_json="Francisco, C", type="input_json_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockDeltaEvent(
            delta=InputJSONDelta(partial_json="A'}", type="input_json_delta"),
            index=0,
            type="content_block_delta",
        ),
        ContentBlockStopEvent(type="content_block_stop", index=0),
        MessageDeltaEvent(
            delta=Delta(stop_reason="tool_use", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=41),
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
            "content": "What is the weather like in San Francisco?",
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
            {"text": "{'location': 'San Francisco, CA'}", "type": "text"}
        ]
    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["data"]
        assert SPANDATA.AI_RESPONSES not in span["data"]

    assert span["measurements"]["ai_prompt_tokens_used"]["value"] == 366
    assert span["measurements"]["ai_completion_tokens_used"]["value"] == 51
    assert span["measurements"]["ai_total_tokens_used"]["value"] == 417
    assert span["data"]["ai.streaming"] is True


@pytest.mark.asyncio
@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="Versions <0.27.0 do not include InputJSONDelta, which was introduced in >=0.27.0 along with a new message delta type for tool calling.",
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
async def test_streaming_create_message_with_input_json_delta_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    client = AsyncAnthropic(api_key="z")
    returned_stream = AsyncStream(cast_to=None, response=None, client=client)
    returned_stream._iterator = async_iterator(
        [
            MessageStartEvent(
                message=Message(
                    id="msg_0",
                    content=[],
                    model="claude-3-5-sonnet-20240620",
                    role="assistant",
                    stop_reason=None,
                    stop_sequence=None,
                    type="message",
                    usage=Usage(input_tokens=366, output_tokens=10),
                ),
                type="message_start",
            ),
            ContentBlockStartEvent(
                type="content_block_start",
                index=0,
                content_block=ToolUseBlock(
                    id="toolu_0", input={}, name="get_weather", type="tool_use"
                ),
            ),
            ContentBlockDeltaEvent(
                delta=InputJSONDelta(partial_json="", type="input_json_delta"),
                index=0,
                type="content_block_delta",
            ),
            ContentBlockDeltaEvent(
                delta=InputJSONDelta(
                    partial_json="{'location':", type="input_json_delta"
                ),
                index=0,
                type="content_block_delta",
            ),
            ContentBlockDeltaEvent(
                delta=InputJSONDelta(partial_json=" 'S", type="input_json_delta"),
                index=0,
                type="content_block_delta",
            ),
            ContentBlockDeltaEvent(
                delta=InputJSONDelta(partial_json="an ", type="input_json_delta"),
                index=0,
                type="content_block_delta",
            ),
            ContentBlockDeltaEvent(
                delta=InputJSONDelta(
                    partial_json="Francisco, C", type="input_json_delta"
                ),
                index=0,
                type="content_block_delta",
            ),
            ContentBlockDeltaEvent(
                delta=InputJSONDelta(partial_json="A'}", type="input_json_delta"),
                index=0,
                type="content_block_delta",
            ),
            ContentBlockStopEvent(type="content_block_stop", index=0),
            MessageDeltaEvent(
                delta=Delta(stop_reason="tool_use", stop_sequence=None),
                usage=MessageDeltaUsage(output_tokens=41),
                type="message_delta",
            ),
        ]
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()
    client.messages._post = AsyncMock(return_value=returned_stream)

    messages = [
        {
            "role": "user",
            "content": "What is the weather like in San Francisco?",
        }
    ]

    with start_transaction(name="anthropic"):
        message = await client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        async for _ in message:
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
            {"text": "{'location': 'San Francisco, CA'}", "type": "text"}
        ]

    else:
        assert SPANDATA.AI_INPUT_MESSAGES not in span["data"]
        assert SPANDATA.AI_RESPONSES not in span["data"]

    assert span["measurements"]["ai_prompt_tokens_used"]["value"] == 366
    assert span["measurements"]["ai_completion_tokens_used"]["value"] == 51
    assert span["measurements"]["ai_total_tokens_used"]["value"] == 417
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


@pytest.mark.asyncio
async def test_exception_message_create_async(sentry_init, capture_events):
    sentry_init(integrations=[AnthropicIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(
        side_effect=AnthropicError("API rate limit reached")
    )
    with pytest.raises(AnthropicError):
        await client.messages.create(
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
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.anthropic"


@pytest.mark.asyncio
async def test_span_origin_async(sentry_init, capture_events):
    sentry_init(
        integrations=[AnthropicIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]

    with start_transaction(name="anthropic"):
        await client.messages.create(max_tokens=1024, messages=messages, model="model")

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.ai.anthropic"


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="Versions <0.27.0 do not include InputJSONDelta.",
)
def test_collect_ai_data_with_input_json_delta():
    event = ContentBlockDeltaEvent(
        delta=InputJSONDelta(partial_json="test", type="input_json_delta"),
        index=0,
        type="content_block_delta",
    )

    input_tokens = 10
    output_tokens = 20
    content_blocks = []

    new_input_tokens, new_output_tokens, new_content_blocks = _collect_ai_data(
        event, input_tokens, output_tokens, content_blocks
    )

    assert new_input_tokens == input_tokens
    assert new_output_tokens == output_tokens
    assert new_content_blocks == ["test"]


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="Versions <0.27.0 do not include InputJSONDelta.",
)
def test_add_ai_data_to_span_with_input_json_delta(sentry_init):
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        span = start_span()
        integration = AnthropicIntegration()

        _add_ai_data_to_span(
            span,
            integration,
            input_tokens=10,
            output_tokens=20,
            content_blocks=["{'test': 'data',", "'more': 'json'}"],
        )

        assert span._data.get(SPANDATA.AI_RESPONSES) == [
            {"type": "text", "text": "{'test': 'data','more': 'json'}"}
        ]
        assert span._data.get("ai.streaming") is True
        assert span._measurements.get("ai_prompt_tokens_used")["value"] == 10
        assert span._measurements.get("ai_completion_tokens_used")["value"] == 20
        assert span._measurements.get("ai_total_tokens_used")["value"] == 30

import pytest
from unittest import mock
import json

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


from anthropic import Anthropic, AnthropicError, AsyncAnthropic, AsyncStream, Stream
from anthropic.types import MessageDeltaUsage, TextDelta, Usage
from anthropic.types.content_block_delta_event import ContentBlockDeltaEvent
from anthropic.types.content_block_start_event import ContentBlockStartEvent
from anthropic.types.content_block_stop_event import ContentBlockStopEvent
from anthropic.types.message import Message
from anthropic.types.message_delta_event import MessageDeltaEvent
from anthropic.types.message_start_event import MessageStartEvent

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
from sentry_sdk.integrations.anthropic import (
    AnthropicIntegration,
    _set_output_data,
    _collect_ai_data,
)
from sentry_sdk.utils import package_version


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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "Hello, Claude"}]'
        )
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False


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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "Hello, Claude"}]'
        )
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False


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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "Hello, Claude"}]'
        )
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 40
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "Hello, Claude"}]'
        )
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 40
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "What is the weather like in San Francisco?"}]'
        )
        assert (
            span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
            == "{'location': 'San Francisco, CA'}"
        )
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 366
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 51
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 417
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "What is the weather like in San Francisco?"}]'
        )
        assert (
            span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
            == "{'location': 'San Francisco, CA'}"
        )

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 366
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 51
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 417
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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

    (event, transaction) = events
    assert event["level"] == "error"
    assert transaction["contexts"]["trace"]["status"] == "internal_error"


def test_span_status_error(sentry_init, capture_events):
    sentry_init(integrations=[AnthropicIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="anthropic"):
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

    (error, transaction) = events
    assert error["level"] == "error"
    assert transaction["spans"][0]["status"] == "internal_error"
    assert transaction["spans"][0]["tags"]["status"] == "internal_error"
    assert transaction["contexts"]["trace"]["status"] == "internal_error"


@pytest.mark.asyncio
async def test_span_status_error_async(sentry_init, capture_events):
    sentry_init(integrations=[AnthropicIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="anthropic"):
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

    (error, transaction) = events
    assert error["level"] == "error"
    assert transaction["spans"][0]["status"] == "internal_error"
    assert transaction["spans"][0]["tags"]["status"] == "internal_error"
    assert transaction["contexts"]["trace"]["status"] == "internal_error"


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

    (event, transaction) = events
    assert event["level"] == "error"
    assert transaction["contexts"]["trace"]["status"] == "internal_error"


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
    model = None
    input_tokens = 10
    output_tokens = 20
    content_blocks = []

    model, new_input_tokens, new_output_tokens, new_content_blocks = _collect_ai_data(
        event, model, input_tokens, output_tokens, content_blocks
    )

    assert model is None
    assert new_input_tokens == input_tokens
    assert new_output_tokens == output_tokens
    assert new_content_blocks == ["test"]


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="Versions <0.27.0 do not include InputJSONDelta.",
)
def test_set_output_data_with_input_json_delta(sentry_init):
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )

    with start_transaction(name="test"):
        span = start_span()
        integration = AnthropicIntegration()
        json_deltas = ["{'test': 'data',", "'more': 'json'}"]
        _set_output_data(
            span,
            integration,
            model="",
            input_tokens=10,
            output_tokens=20,
            content_blocks=[{"text": "".join(json_deltas), "type": "text"}],
        )

        assert (
            span._data.get(SPANDATA.GEN_AI_RESPONSE_TEXT)
            == "{'test': 'data','more': 'json'}"
        )
        assert span._data.get(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS) == 10
        assert span._data.get(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS) == 20
        assert span._data.get(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS) == 30


def test_anthropic_message_role_mapping(sentry_init, capture_events):
    """Test that Anthropic integration properly maps message roles like 'ai' to 'assistant'"""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    client = Anthropic(api_key="z")

    def mock_messages_create(*args, **kwargs):
        return Message(
            id="msg_1",
            content=[TextBlock(text="Hi there!", type="text")],
            model="claude-3-opus",
            role="assistant",
            stop_reason="end_turn",
            stop_sequence=None,
            type="message",
            usage=Usage(input_tokens=10, output_tokens=5),
        )

    client.messages._post = mock.Mock(return_value=mock_messages_create())

    # Test messages with mixed roles including "ai" that should be mapped to "assistant"
    test_messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "ai", "content": "Hi there!"},  # Should be mapped to "assistant"
        {"role": "assistant", "content": "How can I help?"},  # Should stay "assistant"
    ]

    with start_transaction(name="anthropic tx"):
        client.messages.create(
            model="claude-3-opus", max_tokens=10, messages=test_messages
        )

    (event,) = events
    span = event["spans"][0]

    # Verify that the span was created correctly
    assert span["op"] == "gen_ai.chat"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]

    # Parse the stored messages
    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    # Verify that "ai" role was mapped to "assistant"
    assert len(stored_messages) == 4
    assert stored_messages[0]["role"] == "system"
    assert stored_messages[1]["role"] == "user"
    assert (
        stored_messages[2]["role"] == "assistant"
    )  # "ai" should be mapped to "assistant"
    assert stored_messages[3]["role"] == "assistant"  # should stay "assistant"

    # Verify content is preserved
    assert stored_messages[2]["content"] == "Hi there!"
    assert stored_messages[3]["content"] == "How can I help?"

    # Verify no "ai" roles remain
    roles = [msg["role"] for msg in stored_messages]
    assert "ai" not in roles


def test_anthropic_message_truncation(sentry_init, capture_events):
    """Test that large messages are truncated properly in Anthropic integration."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )
    messages = [
        {"role": "user", "content": "small message 1"},
        {"role": "assistant", "content": large_content},
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": "small message 4"},
        {"role": "user", "content": "small message 5"},
    ]

    with start_transaction():
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) > 0
    tx = events[0]
    assert tx["type"] == "transaction"

    chat_spans = [
        span for span in tx.get("spans", []) if span.get("op") == OP.GEN_AI_CHAT
    ]
    assert len(chat_spans) > 0

    chat_span = chat_spans[0]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in chat_span["data"]

    messages_data = chat_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) == 2
    assert "small message 4" in str(parsed_messages[0])
    assert "small message 5" in str(parsed_messages[1])
    assert tx["_meta"]["spans"]["0"]["data"]["gen_ai.request.messages"][""]["len"] == 5


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_nonstreaming_create_message_with_system_prompt(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that system prompts are properly captured in GEN_AI_REQUEST_MESSAGES."""
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
            max_tokens=1024,
            messages=messages,
            model="model",
            system="You are a helpful assistant.",
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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]
        stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
        assert len(stored_messages) == 2
        # System message should be first
        assert stored_messages[0]["role"] == "system"
        assert stored_messages[0]["content"] == "You are a helpful assistant."
        # User message should be second
        assert stored_messages[1]["role"] == "user"
        assert stored_messages[1]["content"] == "Hello, Claude"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False


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
async def test_nonstreaming_create_message_with_system_prompt_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that system prompts are properly captured in GEN_AI_REQUEST_MESSAGES (async)."""
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
            max_tokens=1024,
            messages=messages,
            model="model",
            system="You are a helpful assistant.",
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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]
        stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
        assert len(stored_messages) == 2
        # System message should be first
        assert stored_messages[0]["role"] == "system"
        assert stored_messages[0]["content"] == "You are a helpful assistant."
        # User message should be second
        assert stored_messages[1]["role"] == "user"
        assert stored_messages[1]["content"] == "Hello, Claude"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_streaming_create_message_with_system_prompt(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that system prompts are properly captured in streaming mode."""
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
            max_tokens=1024,
            messages=messages,
            model="model",
            stream=True,
            system="You are a helpful assistant.",
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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]
        stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
        assert len(stored_messages) == 2
        # System message should be first
        assert stored_messages[0]["role"] == "system"
        assert stored_messages[0]["content"] == "You are a helpful assistant."
        # User message should be second
        assert stored_messages[1]["role"] == "user"
        assert stored_messages[1]["content"] == "Hello, Claude"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 40
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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
async def test_streaming_create_message_with_system_prompt_async(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    """Test that system prompts are properly captured in streaming mode (async)."""
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
            max_tokens=1024,
            messages=messages,
            model="model",
            stream=True,
            system="You are a helpful assistant.",
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

    assert span["op"] == OP.GEN_AI_CHAT
    assert span["description"] == "chat model"
    assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]
        stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
        assert len(stored_messages) == 2
        # System message should be first
        assert stored_messages[0]["role"] == "system"
        assert stored_messages[0]["content"] == "You are a helpful assistant."
        # User message should be second
        assert stored_messages[1]["role"] == "user"
        assert stored_messages[1]["content"] == "Hello, Claude"
        assert span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["data"]

    assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 30
    assert span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 40
    assert span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


def test_system_prompt_with_complex_structure(sentry_init, capture_events):
    """Test that complex system prompt structures (list of text blocks) are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    # System prompt as list of text blocks
    system_prompt = [
        {"type": "text", "text": "You are a helpful assistant."},
        {"type": "text", "text": "Be concise and clear."},
    ]

    messages = [
        {
            "role": "user",
            "content": "Hello",
        }
    ]

    with start_transaction(name="anthropic"):
        response = client.messages.create(
            max_tokens=1024, messages=messages, model="model", system=system_prompt
        )

    assert response == EXAMPLE_MESSAGE
    assert len(events) == 1
    (event,) = events

    assert len(event["spans"]) == 1
    (span,) = event["spans"]

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]
    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    # Should have system message first, then user message
    assert len(stored_messages) == 2
    assert stored_messages[0]["role"] == "system"
    # System content should be a list of text blocks
    assert isinstance(stored_messages[0]["content"], list)
    assert len(stored_messages[0]["content"]) == 2
    assert stored_messages[0]["content"][0]["type"] == "text"
    assert stored_messages[0]["content"][0]["text"] == "You are a helpful assistant."
    assert stored_messages[0]["content"][1]["type"] == "text"
    assert stored_messages[0]["content"][1]["text"] == "Be concise and clear."
    assert stored_messages[1]["role"] == "user"
    assert stored_messages[1]["content"] == "Hello"

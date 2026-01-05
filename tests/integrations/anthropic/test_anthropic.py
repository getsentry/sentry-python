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
    _transform_content_block,
    _transform_message_content,
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert transaction["spans"][0]["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


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
    assert transaction["spans"][0]["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


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
    assert event["spans"][0]["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


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
    assert event["spans"][0]["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert chat_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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
    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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

    assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
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


# Tests for _transform_content_block helper function


def test_transform_content_block_base64_image():
    """Test that base64 encoded images are transformed to blob format."""
    content_block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "base64encodeddata...",
        },
    }

    result = _transform_content_block(content_block)

    assert result == {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/jpeg",
        "content": "base64encodeddata...",
    }


def test_transform_content_block_url_image():
    """Test that URL-referenced images are transformed to uri format."""
    content_block = {
        "type": "image",
        "source": {
            "type": "url",
            "url": "https://example.com/image.jpg",
        },
    }

    result = _transform_content_block(content_block)

    assert result == {
        "type": "uri",
        "modality": "image",
        "mime_type": "",
        "uri": "https://example.com/image.jpg",
    }


def test_transform_content_block_file_image():
    """Test that file_id-referenced images are transformed to file format."""
    content_block = {
        "type": "image",
        "source": {
            "type": "file",
            "file_id": "file_abc123",
        },
    }

    result = _transform_content_block(content_block)

    assert result == {
        "type": "file",
        "modality": "image",
        "mime_type": "",
        "file_id": "file_abc123",
    }


def test_transform_content_block_base64_document():
    """Test that base64 encoded PDFs are transformed to blob format."""
    content_block = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "base64encodedpdfdata...",
        },
    }

    result = _transform_content_block(content_block)

    assert result == {
        "type": "blob",
        "modality": "document",
        "mime_type": "application/pdf",
        "content": "base64encodedpdfdata...",
    }


def test_transform_content_block_url_document():
    """Test that URL-referenced documents are transformed to uri format."""
    content_block = {
        "type": "document",
        "source": {
            "type": "url",
            "url": "https://example.com/document.pdf",
        },
    }

    result = _transform_content_block(content_block)

    assert result == {
        "type": "uri",
        "modality": "document",
        "mime_type": "",
        "uri": "https://example.com/document.pdf",
    }


def test_transform_content_block_file_document():
    """Test that file_id-referenced documents are transformed to file format."""
    content_block = {
        "type": "document",
        "source": {
            "type": "file",
            "file_id": "file_doc456",
            "media_type": "application/pdf",
        },
    }

    result = _transform_content_block(content_block)

    assert result == {
        "type": "file",
        "modality": "document",
        "mime_type": "application/pdf",
        "file_id": "file_doc456",
    }


def test_transform_content_block_text_document():
    """Test that plain text documents are transformed correctly."""
    content_block = {
        "type": "document",
        "source": {
            "type": "text",
            "media_type": "text/plain",
            "data": "This is plain text content.",
        },
    }

    result = _transform_content_block(content_block)

    assert result == {
        "type": "text",
        "text": "This is plain text content.",
    }


def test_transform_content_block_text_block():
    """Test that regular text blocks are returned as-is."""
    content_block = {
        "type": "text",
        "text": "Hello, world!",
    }

    result = _transform_content_block(content_block)

    assert result == content_block


def test_transform_message_content_string():
    """Test that string content is returned as-is."""
    result = _transform_message_content("Hello, world!")
    assert result == "Hello, world!"


def test_transform_message_content_list():
    """Test that list content is transformed correctly."""
    content = [
        {"type": "text", "text": "Hello!"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "base64data...",
            },
        },
    ]

    result = _transform_message_content(content)

    assert len(result) == 2
    assert result[0] == {"type": "text", "text": "Hello!"}
    assert result[1] == {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/png",
        "content": "base64data...",
    }


# Integration tests for binary data in messages


def test_message_with_base64_image(sentry_init, capture_events):
    """Test that messages with base64 images are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "base64encodeddatahere...",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["data"]
    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    assert len(stored_messages) == 1
    assert stored_messages[0]["role"] == "user"
    content = stored_messages[0]["content"]
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "What's in this image?"}
    assert content[1] == {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/jpeg",
        "content": "base64encodeddatahere...",
    }


def test_message_with_url_image(sentry_init, capture_events):
    """Test that messages with URL-referenced images are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image."},
                {
                    "type": "image",
                    "source": {
                        "type": "url",
                        "url": "https://example.com/photo.png",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "uri",
        "modality": "image",
        "mime_type": "",
        "uri": "https://example.com/photo.png",
    }


def test_message_with_file_image(sentry_init, capture_events):
    """Test that messages with file_id-referenced images are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What do you see?"},
                {
                    "type": "image",
                    "source": {
                        "type": "file",
                        "file_id": "file_img_12345",
                        "media_type": "image/webp",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "file",
        "modality": "image",
        "mime_type": "image/webp",
        "file_id": "file_img_12345",
    }


def test_message_with_base64_pdf(sentry_init, capture_events):
    """Test that messages with base64-encoded PDF documents are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Summarize this document."},
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": "JVBERi0xLjQKJeLj...base64pdfdata",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "blob",
        "modality": "document",
        "mime_type": "application/pdf",
        "content": "JVBERi0xLjQKJeLj...base64pdfdata",
    }


def test_message_with_url_pdf(sentry_init, capture_events):
    """Test that messages with URL-referenced PDF documents are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this PDF?"},
                {
                    "type": "document",
                    "source": {
                        "type": "url",
                        "url": "https://example.com/report.pdf",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "uri",
        "modality": "document",
        "mime_type": "",
        "uri": "https://example.com/report.pdf",
    }


def test_message_with_file_document(sentry_init, capture_events):
    """Test that messages with file_id-referenced documents are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this document."},
                {
                    "type": "document",
                    "source": {
                        "type": "file",
                        "file_id": "file_doc_67890",
                        "media_type": "application/pdf",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "file",
        "modality": "document",
        "mime_type": "application/pdf",
        "file_id": "file_doc_67890",
    }


def test_message_with_mixed_content(sentry_init, capture_events):
    """Test that messages with mixed content (text, images, documents) are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Compare this image with the document."},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "iVBORw0KGgo...base64imagedata",
                    },
                },
                {
                    "type": "image",
                    "source": {
                        "type": "url",
                        "url": "https://example.com/comparison.jpg",
                    },
                },
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": "JVBERi0xLjQK...base64pdfdata",
                    },
                },
                {"type": "text", "text": "Please provide a detailed analysis."},
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    content = stored_messages[0]["content"]

    assert len(content) == 5
    assert content[0] == {
        "type": "text",
        "text": "Compare this image with the document.",
    }
    assert content[1] == {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/png",
        "content": "iVBORw0KGgo...base64imagedata",
    }
    assert content[2] == {
        "type": "uri",
        "modality": "image",
        "mime_type": "",
        "uri": "https://example.com/comparison.jpg",
    }
    assert content[3] == {
        "type": "blob",
        "modality": "document",
        "mime_type": "application/pdf",
        "content": "JVBERi0xLjQK...base64pdfdata",
    }
    assert content[4] == {
        "type": "text",
        "text": "Please provide a detailed analysis.",
    }


def test_message_with_multiple_images_different_formats(sentry_init, capture_events):
    """Test that messages with multiple images of different source types are handled."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "base64data1...",
                    },
                },
                {
                    "type": "image",
                    "source": {
                        "type": "url",
                        "url": "https://example.com/img2.gif",
                    },
                },
                {
                    "type": "image",
                    "source": {
                        "type": "file",
                        "file_id": "file_img_789",
                        "media_type": "image/webp",
                    },
                },
                {"type": "text", "text": "Compare these three images."},
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    stored_messages = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    content = stored_messages[0]["content"]

    assert len(content) == 4
    assert content[0] == {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/jpeg",
        "content": "base64data1...",
    }
    assert content[1] == {
        "type": "uri",
        "modality": "image",
        "mime_type": "",
        "uri": "https://example.com/img2.gif",
    }
    assert content[2] == {
        "type": "file",
        "modality": "image",
        "mime_type": "image/webp",
        "file_id": "file_img_789",
    }
    assert content[3] == {"type": "text", "text": "Compare these three images."}


def test_binary_content_not_stored_when_pii_disabled(sentry_init, capture_events):
    """Test that binary content is not stored when send_default_pii is False."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=False,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "base64encodeddatahere...",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    # Messages should not be stored
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]


def test_binary_content_not_stored_when_prompts_disabled(sentry_init, capture_events):
    """Test that binary content is not stored when include_prompts is False."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=False)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()
    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "base64encodeddatahere...",
                    },
                },
            ],
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    # Messages should not be stored
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["data"]

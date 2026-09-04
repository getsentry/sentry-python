import json
from unittest import mock

import pytest

import sentry_sdk

try:
    from unittest.mock import AsyncMock
except ImportError:

    class AsyncMock(mock.MagicMock):
        async def __call__(self, *args, **kwargs):
            return super(AsyncMock, self).__call__(*args, **kwargs)


from anthropic import Anthropic, AnthropicError, AsyncAnthropic
from anthropic.types import MessageDeltaUsage, TextDelta, Usage
from anthropic.types.content_block_delta_event import ContentBlockDeltaEvent
from anthropic.types.content_block_start_event import ContentBlockStartEvent
from anthropic.types.content_block_stop_event import ContentBlockStopEvent
from anthropic.types.message import Message
from anthropic.types.message_delta_event import MessageDeltaEvent
from anthropic.types.message_start_event import MessageStartEvent

try:
    from anthropic import APIStatusError
    from anthropic.types import ErrorResponse, OverloadedError
except ImportError:
    ErrorResponse = None
    OverloadedError = None
    APIStatusError = None

try:
    from anthropic.types import InputJSONDelta
except ImportError:
    try:
        from anthropic.types import InputJsonDelta as InputJSONDelta
    except ImportError:
        pass

try:
    from anthropic.lib.streaming import TextEvent
except ImportError:
    TextEvent = None

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

from sentry_sdk.ai.utils import transform_content_part, transform_message_content
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.anthropic import (
    AnthropicIntegration,
    _collect_ai_data,
    _RecordedUsage,
    _set_output_data,
    _transform_anthropic_content_block,
)
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.traces import SpanStatus
from sentry_sdk.utils import package_version

ANTHROPIC_VERSION = package_version("anthropic")

EXAMPLE_MESSAGE = Message(
    id="msg_01XFDUDYJgAACzvnptvVoYEL",
    model="model",
    role="assistant",
    content=[TextBlock(type="text", text="Hi, I'm Claude.")],
    type="message",
    stop_reason="end_turn",
    usage=Usage(input_tokens=10, output_tokens=20),
)


DATA_COLLECTION_EXAMPLE_TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather in a given location",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    }
]

DATA_COLLECTION_EXPECTED_INPUT_DATA = {
    SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS: [
        {"type": "text", "content": "You are a helpful assistant."}
    ],
    SPANDATA.GEN_AI_REQUEST_MESSAGES: [{"role": "user", "content": "Hello, Claude"}],
}

DATA_COLLECTION_INPUT_DATA_KEYS = list(DATA_COLLECTION_EXPECTED_INPUT_DATA.keys())

DATA_COLLECTION_EXPECTED_RESPONSE_TEXT = "Let me check the weather."

DATA_COLLECTION_EXPECTED_TOOL_CALLS = [
    {
        "id": "toolu_01A09q90qw90lq917835lq9",
        "input": {"location": "San Francisco, CA"},
        "name": "get_weather",
        "type": "tool_use",
    }
]


def data_collection_tool_use_message():
    return Message(
        id="msg_01XFDUDYJgAACzvnptvVoYEL",
        model="model",
        role="assistant",
        content=[
            TextBlock(type="text", text=DATA_COLLECTION_EXPECTED_RESPONSE_TEXT),
            ToolUseBlock(
                id="toolu_01A09q90qw90lq917835lq9",
                input={"location": "San Francisco, CA"},
                name="get_weather",
                type="tool_use",
            ),
        ],
        type="message",
        stop_reason="tool_use",
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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Message demonstrating the absence of truncation.",
        },
        {
            "role": "user",
            "content": "Hello, Claude",
        },
    ]
    items = capture_items("transaction", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
        response = client.messages.create(
            max_tokens=1024, messages=messages, model="model"
        )

    assert response == EXAMPLE_MESSAGE
    usage = response.usage

    assert usage.input_tokens == 10
    assert usage.output_tokens == 20

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]) == [
            {
                "role": "user",
                "content": "Message demonstrating the absence of truncation.",
            },
            {
                "role": "user",
                "content": "Hello, Claude",
            },
        ]
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["end_turn"]


@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,expected_present,expected_absent",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            False,
            False,
            DATA_COLLECTION_EXPECTED_INPUT_DATA,
            [],
            id="gen-ai-inputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            True,
            True,
            {},
            DATA_COLLECTION_INPUT_DATA_KEYS,
            id="gen-ai-inputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {}},
            True,
            True,
            DATA_COLLECTION_EXPECTED_INPUT_DATA,
            [],
            id="gen-ai-inputs-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            True,
            True,
            DATA_COLLECTION_EXPECTED_INPUT_DATA,
            [],
            id="legacy-pii-and-include-prompts-enabled",
        ),
    ],
)
def test_nonstreaming_create_message_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    expected_present,
    expected_absent,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        system="You are a helpful assistant.",
        messages=[{"role": "user", "content": "Hello, Claude"}],
    )
    items = capture_items("transaction", "span")

    client.messages.create(**create_kwargs)

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    assert span_data[SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span_data[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024

    for key, expected_value in expected_present.items():
        assert json.loads(span_data[key]) == expected_value

    for key in expected_absent:
        assert key not in span_data


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="Tools are not supported in this version of the anthropic package",
)
@pytest.mark.parametrize(
    "data_collection,tools_collected",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            True,
            id="gen-ai-inputs-enabled-tools-collected",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            False,
            id="gen-ai-inputs-disabled-tools-not-collected",
        ),
        pytest.param(
            None,
            True,
            id="legacy-pii-disabled-tools-still-collected",
        ),
    ],
)
def test_nonstreaming_create_message_data_collection_tools(
    sentry_init,
    capture_items,
    data_collection,
    tools_collected,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=False)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        messages=[],
        tools=DATA_COLLECTION_EXAMPLE_TOOLS,
    )
    items = capture_items("transaction", "span")

    client.messages.create(**create_kwargs)

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    if tools_collected:
        assert (
            json.loads(span_data[SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS])
            == DATA_COLLECTION_EXAMPLE_TOOLS
        )
    else:
        assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS not in span_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,expected_present,expected_absent",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            False,
            False,
            DATA_COLLECTION_EXPECTED_INPUT_DATA,
            [],
            id="gen-ai-inputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            True,
            True,
            {},
            DATA_COLLECTION_INPUT_DATA_KEYS,
            id="gen-ai-inputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            None,
            True,
            True,
            DATA_COLLECTION_EXPECTED_INPUT_DATA,
            [],
            id="legacy-pii-and-include-prompts-enabled",
        ),
    ],
)
async def test_nonstreaming_create_message_data_collection_async(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    expected_present,
    expected_absent,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(return_value=EXAMPLE_MESSAGE)

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        system="You are a helpful assistant.",
        messages=[{"role": "user", "content": "Hello, Claude"}],
    )
    items = capture_items("transaction", "span")

    await client.messages.create(**create_kwargs)

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    assert span_data[SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span_data[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024

    for key, expected_value in expected_present.items():
        assert json.loads(span_data[key]) == expected_value

    for key in expected_absent:
        assert key not in span_data


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="anthropic.types.ToolUseBlock was added in 0.27.0. Before that, tool use was only available under the beta namespace and could not appear in a standard Message.",
)
@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,outputs_collected",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False, "outputs": False}},
            True,
            True,
            False,
            id="gen-ai-inputs-and-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {}},
            False,
            False,
            True,
            id="gen-ai-inputs-and-outputs-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            id="legacy-pii-and-include-prompts-enabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            id="legacy-pii-disabled",
        ),
    ],
)
def test_nonstreaming_create_message_data_collection_outputs(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    outputs_collected,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=data_collection_tool_use_message())

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        messages=[{"role": "user", "content": "What is the weather in San Francisco?"}],
        tools=DATA_COLLECTION_EXAMPLE_TOOLS,
    )
    items = capture_items("transaction", "span")

    client.messages.create(**create_kwargs)

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    # Output data that is not gated on data collection
    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_ID] == "msg_01XFDUDYJgAACzvnptvVoYEL"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["tool_use"]
    assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span_data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20

    if outputs_collected:
        assert (
            span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
            == DATA_COLLECTION_EXPECTED_RESPONSE_TEXT
        )
        assert (
            json.loads(span_data[SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS])
            == DATA_COLLECTION_EXPECTED_TOOL_CALLS
        )
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in span_data


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="anthropic.types.ToolUseBlock was added in 0.27.0. Before that, tool use was only available under the beta namespace and could not appear in a standard Message.",
)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,outputs_collected",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False, "outputs": False}},
            True,
            True,
            False,
            id="gen-ai-inputs-and-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {}},
            False,
            False,
            True,
            id="gen-ai-inputs-and-outputs-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            id="legacy-pii-and-include-prompts-enabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            id="legacy-pii-disabled",
        ),
    ],
)
async def test_nonstreaming_create_message_data_collection_outputs_async(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    outputs_collected,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(return_value=data_collection_tool_use_message())

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        messages=[{"role": "user", "content": "What is the weather in San Francisco?"}],
        tools=DATA_COLLECTION_EXAMPLE_TOOLS,
    )
    items = capture_items("transaction", "span")

    await client.messages.create(**create_kwargs)

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    # Output data that is not gated on data collection
    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_ID] == "msg_01XFDUDYJgAACzvnptvVoYEL"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["tool_use"]
    assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span_data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20

    if outputs_collected:
        assert (
            span_data[SPANDATA.GEN_AI_RESPONSE_TEXT]
            == DATA_COLLECTION_EXPECTED_RESPONSE_TEXT
        )
        assert (
            json.loads(span_data[SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS])
            == DATA_COLLECTION_EXPECTED_TOOL_CALLS
        )
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data
        assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS not in span_data


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Message demonstrating the absence of truncation.",
        },
        {
            "role": "user",
            "content": "Hello, Claude",
        },
    ]
    items = capture_items("transaction", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
        response = await client.messages.create(
            max_tokens=1024, messages=messages, model="model"
        )

    assert response == EXAMPLE_MESSAGE
    usage = response.usage

    assert usage.input_tokens == 10
    assert usage.output_tokens == 20

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]) == [
            {
                "role": "user",
                "content": "Message demonstrating the absence of truncation.",
            },
            {
                "role": "user",
                "content": "Hello, Claude",
            },
        ]
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
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
def test_streaming_create_message(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                    delta=Delta(stop_reason="max_tokens"),
                    usage=MessageDeltaUsage(output_tokens=10),
                    type="message_delta",
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Message demonstrating the absence of truncation.",
        },
        {
            "role": "user",
            "content": "Hello, Claude",
        },
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]) == [
            {
                "role": "user",
                "content": "Message demonstrating the absence of truncation.",
            },
            {
                "role": "user",
                "content": "Hello, Claude",
            },
        ]
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["max_tokens"]


@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,expected_present,expected_absent",
    [
        pytest.param(
            {"gen_ai": {"inputs": True}},
            False,
            False,
            DATA_COLLECTION_EXPECTED_INPUT_DATA,
            [],
            id="gen-ai-inputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"inputs": False}},
            True,
            True,
            {},
            DATA_COLLECTION_INPUT_DATA_KEYS,
            id="gen-ai-inputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            None,
            True,
            True,
            DATA_COLLECTION_EXPECTED_INPUT_DATA,
            [],
            id="legacy-pii-and-include-prompts-enabled",
        ),
    ],
)
def test_streaming_create_message_data_collection(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    expected_present,
    expected_absent,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                ContentBlockStopEvent(type="content_block_stop", index=0),
                MessageDeltaEvent(
                    delta=Delta(stop_reason="max_tokens"),
                    usage=MessageDeltaUsage(output_tokens=10),
                    type="message_delta",
                ),
            ]
        )
    )

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        system="You are a helpful assistant.",
        messages=[{"role": "user", "content": "Hello, Claude"}],
        stream=True,
    )
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ):
        message = client.messages.create(**create_kwargs)
        for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    assert span_data[SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span_data[SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 1024
    assert span_data[SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    for key, expected_value in expected_present.items():
        assert json.loads(span_data[key]) == expected_value

    for key in expected_absent:
        assert key not in span_data


@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,outputs_collected",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {}},
            False,
            False,
            True,
            id="gen-ai-outputs-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            id="legacy-pii-and-include-prompts-enabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            id="legacy-pii-disabled",
        ),
    ],
)
def test_streaming_create_message_data_collection_outputs(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    outputs_collected,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                    delta=TextDelta(text="! I'm Claude!", type="text_delta"),
                    index=0,
                    type="content_block_delta",
                ),
                ContentBlockStopEvent(type="content_block_stop", index=0),
                MessageDeltaEvent(
                    delta=Delta(stop_reason="max_tokens"),
                    usage=MessageDeltaUsage(output_tokens=10),
                    type="message_delta",
                ),
            ]
        )
    )

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        messages=[{"role": "user", "content": "Hello, Claude"}],
        stream=True,
    )
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ):
        message = client.messages.create(**create_kwargs)
        for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    # Output data that is not gated on data collection
    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_ID] == "msg_01XFDUDYJgAACzvnptvVoYEL"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["max_tokens"]
    assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span_data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10

    if outputs_collected:
        assert span_data[SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


def test_streaming_create_message_close(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                    delta=Delta(stop_reason="max_tokens"),
                    usage=MessageDeltaUsage(output_tokens=10),
                    type="message_delta",
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        messages = client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        for _ in range(4):
            next(messages)

        messages.close()

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 41),
    reason="Error classes moved in https://github.com/anthropics/anthropic-sdk-python/commit/4e0b15e22fe40e9aa513459564f641bf97c90954.",
)
def test_streaming_create_message_api_error(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                ErrorResponse(
                    type="error",
                    error=OverloadedError(
                        message="Overloaded", type="overloaded_error"
                    ),
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with pytest.raises(APIStatusError), mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    assert spans[1]["status"] == SpanStatus.ERROR
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )

    assert span["status"] == "error"


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_stream_messages(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                    delta=Delta(stop_reason="max_tokens"),
                    usage=MessageDeltaUsage(output_tokens=10),
                    type="message_delta",
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Message demonstrating the absence of truncation.",
        },
        {
            "role": "user",
            "content": "Hello, Claude",
        },
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"), client.messages.stream(
        max_tokens=1024,
        messages=messages,
        model="model",
    ) as stream:
        for event in stream:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]) == [
            {
                "role": "user",
                "content": "Message demonstrating the absence of truncation.",
            },
            {
                "role": "user",
                "content": "Hello, Claude",
            },
        ]
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["max_tokens"]


@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,outputs_collected",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {}},
            False,
            False,
            True,
            id="gen-ai-outputs-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            id="legacy-pii-and-include-prompts-enabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            id="legacy-pii-disabled",
        ),
    ],
)
def test_stream_messages_data_collection_outputs(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    outputs_collected,
    get_model_response,
    server_side_event_chunks,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                    delta=TextDelta(text="! I'm Claude!", type="text_delta"),
                    index=0,
                    type="content_block_delta",
                ),
                ContentBlockStopEvent(type="content_block_stop", index=0),
                MessageDeltaEvent(
                    delta=Delta(stop_reason="max_tokens"),
                    usage=MessageDeltaUsage(output_tokens=10),
                    type="message_delta",
                ),
            ]
        )
    )

    stream_kwargs = dict(
        max_tokens=1024,
        model="model",
        messages=[{"role": "user", "content": "Hello, Claude"}],
    )
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ), client.messages.stream(**stream_kwargs) as stream:
        for _ in stream:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    # Output data that is not gated on data collection
    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_ID] == "msg_01XFDUDYJgAACzvnptvVoYEL"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["max_tokens"]
    assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span_data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10

    if outputs_collected:
        assert span_data[SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


def test_stream_messages_close(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                    delta=Delta(stop_reason="max_tokens"),
                    usage=MessageDeltaUsage(output_tokens=10),
                    type="message_delta",
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"), client.messages.stream(
        max_tokens=1024,
        messages=messages,
        model="model",
    ) as stream:
        for _ in range(4):
            next(stream)

        # New versions add TextEvent, so consume one more event.
        if TextEvent is not None and isinstance(next(stream), TextEvent):
            next(stream)

        stream.close()

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 41),
    reason="Error classes moved in https://github.com/anthropics/anthropic-sdk-python/commit/4e0b15e22fe40e9aa513459564f641bf97c90954.",
)
def test_stream_messages_api_error(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                ErrorResponse(
                    type="error",
                    error=OverloadedError(
                        message="Overloaded", type="overloaded_error"
                    ),
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with pytest.raises(APIStatusError), mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"), client.messages.stream(
        max_tokens=1024,
        messages=messages,
        model="model",
    ) as stream:
        for event in stream:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    assert spans[1]["status"] == SpanStatus.ERROR
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )

    assert span["status"] == "error"


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                        delta=Delta(stop_reason="max_tokens"),
                        usage=MessageDeltaUsage(output_tokens=10),
                        type="message_delta",
                    ),
                ]
            )
        ),
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        default_integrations=False,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Message demonstrating the absence of truncation.",
        },
        {
            "role": "user",
            "content": "Hello, Claude",
        },
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = await client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        async for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]) == [
            {
                "role": "user",
                "content": "Message demonstrating the absence of truncation.",
            },
            {
                "role": "user",
                "content": "Hello, Claude",
            },
        ]
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["max_tokens"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,outputs_collected",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {}},
            False,
            False,
            True,
            id="gen-ai-outputs-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            id="legacy-pii-and-include-prompts-enabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            id="legacy-pii-disabled",
        ),
    ],
)
async def test_streaming_create_message_data_collection_outputs_async(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    outputs_collected,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                        delta=TextDelta(text="! I'm Claude!", type="text_delta"),
                        index=0,
                        type="content_block_delta",
                    ),
                    ContentBlockStopEvent(type="content_block_stop", index=0),
                    MessageDeltaEvent(
                        delta=Delta(stop_reason="max_tokens"),
                        usage=MessageDeltaUsage(output_tokens=10),
                        type="message_delta",
                    ),
                ]
            )
        ),
    )

    create_kwargs = dict(
        max_tokens=1024,
        model="model",
        messages=[{"role": "user", "content": "Hello, Claude"}],
        stream=True,
    )
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ):
        message = await client.messages.create(**create_kwargs)
        async for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    # Output data that is not gated on data collection
    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_ID] == "msg_01XFDUDYJgAACzvnptvVoYEL"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["max_tokens"]
    assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span_data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10

    if outputs_collected:
        assert span_data[SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


@pytest.mark.asyncio
async def test_streaming_create_message_async_close(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                        delta=Delta(stop_reason="max_tokens"),
                        usage=MessageDeltaUsage(output_tokens=10),
                        type="message_delta",
                    ),
                ]
            )
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        messages = await client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        for _ in range(4):
            await messages.__anext__()
        await messages.close()

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 41),
    reason="Error classes moved in https://github.com/anthropics/anthropic-sdk-python/commit/4e0b15e22fe40e9aa513459564f641bf97c90954.",
)
@pytest.mark.asyncio
async def test_streaming_create_message_async_api_error(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                    ErrorResponse(
                        type="error",
                        error=OverloadedError(
                            message="Overloaded", type="overloaded_error"
                        ),
                    ),
                ]
            )
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with pytest.raises(APIStatusError), mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = await client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        async for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    assert spans[1]["status"] == SpanStatus.ERROR
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )

    assert span["status"] == "error"


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
async def test_stream_message_async(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
        ),
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Message demonstrating the absence of truncation.",
        },
        {
            "role": "user",
            "content": "Hello, Claude",
        },
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        async with client.messages.stream(
            max_tokens=1024,
            messages=messages,
            model="model",
        ) as stream:
            async for event in stream:
                pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]) == [
            {
                "role": "user",
                "content": "Message demonstrating the absence of truncation.",
            },
            {
                "role": "user",
                "content": "Hello, Claude",
            },
        ]
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_collection,send_default_pii,include_prompts,outputs_collected",
    [
        pytest.param(
            {"gen_ai": {"outputs": True}},
            False,
            False,
            True,
            id="gen-ai-outputs-enabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {"outputs": False}},
            True,
            True,
            False,
            id="gen-ai-outputs-disabled-overrides-pii-and-include-prompts",
        ),
        pytest.param(
            {"gen_ai": {}},
            False,
            False,
            True,
            id="gen-ai-outputs-omitted-defaults-to-enabled",
        ),
        pytest.param(
            None,
            True,
            True,
            True,
            id="legacy-pii-and-include-prompts-enabled",
        ),
        pytest.param(
            None,
            False,
            True,
            False,
            id="legacy-pii-disabled",
        ),
    ],
)
async def test_stream_messages_data_collection_outputs_async(
    sentry_init,
    capture_items,
    data_collection,
    send_default_pii,
    include_prompts,
    outputs_collected,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    sentry_init_kwargs = dict(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )
    if data_collection is not None:
        sentry_init_kwargs["_experiments"] = {"data_collection": data_collection}
    sentry_init(**sentry_init_kwargs)

    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                        delta=TextDelta(text="! I'm Claude!", type="text_delta"),
                        index=0,
                        type="content_block_delta",
                    ),
                    ContentBlockStopEvent(type="content_block_stop", index=0),
                    MessageDeltaEvent(
                        delta=Delta(stop_reason="max_tokens"),
                        usage=MessageDeltaUsage(output_tokens=10),
                        type="message_delta",
                    ),
                ]
            )
        ),
    )

    stream_kwargs = dict(
        max_tokens=1024,
        model="model",
        messages=[{"role": "user", "content": "Hello, Claude"}],
    )
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ):
        async with client.messages.stream(**stream_kwargs) as stream:
            async for _ in stream:
                pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = [s for s in spans if s["attributes"]["sentry.op"] == OP.GEN_AI_CHAT]
    span_data = span["attributes"]

    # Output data that is not gated on data collection
    assert span_data[SPANDATA.GEN_AI_RESPONSE_MODEL] == "model"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_ID] == "msg_01XFDUDYJgAACzvnptvVoYEL"
    assert span_data[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["max_tokens"]
    assert span_data[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span_data[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10

    if outputs_collected:
        assert span_data[SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span_data


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 41),
    reason="Error classes moved in https://github.com/anthropics/anthropic-sdk-python/commit/4e0b15e22fe40e9aa513459564f641bf97c90954.",
)
@pytest.mark.asyncio
async def test_stream_messages_async_api_error(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                    ErrorResponse(
                        type="error",
                        error=OverloadedError(
                            message="Overloaded", type="overloaded_error"
                        ),
                    ),
                ]
            )
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with pytest.raises(APIStatusError), mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        async with client.messages.stream(
            max_tokens=1024,
            messages=messages,
            model="model",
        ) as stream:
            async for event in stream:
                pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )

    assert span["status"] == "error"


@pytest.mark.asyncio
async def test_stream_messages_async_close(
    sentry_init,
    capture_items,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                        delta=Delta(stop_reason="max_tokens"),
                        usage=MessageDeltaUsage(output_tokens=10),
                        type="message_delta",
                    ),
                ]
            )
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        async with client.messages.stream(
            max_tokens=1024,
            messages=messages,
            model="model",
        ) as stream:
            for _ in range(4):
                await stream.__anext__()

            # New versions add TextEvent, so consume one more event.
            if TextEvent is not None and isinstance(
                await stream.__anext__(), TextEvent
            ):
                await stream.__anext__()

            await stream.close()

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["name"] == "anthropic"
    span = next(
        span for span in spans if span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    )

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    assert (
        span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        == '[{"role": "user", "content": "Hello, Claude"}]'
    )
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi!"

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert (
        span["attributes"][SPANDATA.GEN_AI_RESPONSE_ID]
        == "msg_01XFDUDYJgAACzvnptvVoYEL"
    )


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                        partial_json='{"location": "', type="input_json_delta"
                    ),
                    index=0,
                    type="content_block_delta",
                ),
                ContentBlockDeltaEvent(
                    delta=InputJSONDelta(partial_json="S", type="input_json_delta"),
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
                    delta=InputJSONDelta(partial_json='A"}', type="input_json_delta"),
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
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "What is the weather like in San Francisco?",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "What is the weather like in San Francisco?"}]'
        )
        assert (
            span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]
            == '{"location": "San Francisco, CA"}'
        )
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 366
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 41
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 407
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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
def test_stream_messages_with_input_json_delta(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
                        partial_json='{"location": "', type="input_json_delta"
                    ),
                    index=0,
                    type="content_block_delta",
                ),
                ContentBlockDeltaEvent(
                    delta=InputJSONDelta(partial_json="S", type="input_json_delta"),
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
                    delta=InputJSONDelta(partial_json='A"}', type="input_json_delta"),
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
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "What is the weather like in San Francisco?",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"), client.messages.stream(
        max_tokens=1024,
        messages=messages,
        model="model",
    ) as stream:
        for event in stream:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "What is the weather like in San Francisco?"}]'
        )
        assert (
            span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]
            == '{"location": "San Francisco, CA"}'
        )
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 366
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 41
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 407
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")
    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                            partial_json='{"location": "', type="input_json_delta"
                        ),
                        index=0,
                        type="content_block_delta",
                    ),
                    ContentBlockDeltaEvent(
                        delta=InputJSONDelta(partial_json="S", type="input_json_delta"),
                        index=0,
                        type="content_block_delta",
                    ),
                    ContentBlockDeltaEvent(
                        delta=InputJSONDelta(
                            partial_json="an ", type="input_json_delta"
                        ),
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
                        delta=InputJSONDelta(
                            partial_json='A"}', type="input_json_delta"
                        ),
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
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "What is the weather like in San Francisco?",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = await client.messages.create(
            max_tokens=1024, messages=messages, model="model", stream=True
        )

        async for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "What is the weather like in San Francisco?"}]'
        )
        assert (
            span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]
            == '{"location": "San Francisco, CA"}'
        )

    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 366
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 41
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 407
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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
async def test_stream_message_with_input_json_delta_async(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    client = AsyncAnthropic(api_key="z")
    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
                            partial_json='{"location": "', type="input_json_delta"
                        ),
                        index=0,
                        type="content_block_delta",
                    ),
                    ContentBlockDeltaEvent(
                        delta=InputJSONDelta(partial_json="S", type="input_json_delta"),
                        index=0,
                        type="content_block_delta",
                    ),
                    ContentBlockDeltaEvent(
                        delta=InputJSONDelta(
                            partial_json="an ", type="input_json_delta"
                        ),
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
                        delta=InputJSONDelta(
                            partial_json='A"}', type="input_json_delta"
                        ),
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
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "What is the weather like in San Francisco?",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        async with client.messages.stream(
            max_tokens=1024,
            messages=messages,
            model="model",
        ) as stream:
            async for event in stream:
                pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert (
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
            == '[{"role": "user", "content": "What is the weather like in San Francisco?"}]'
        )
        assert (
            span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT]
            == '{"location": "San Francisco, CA"}'
        )
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 366
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 41
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 407
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


def test_exception_message_create(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(
        side_effect=AnthropicError("API rate limit reached")
    )
    items = capture_items("event")

    with pytest.raises(AnthropicError):
        client.messages.create(
            model="some-model",
            messages=[{"role": "system", "content": "I'm throwing an exception"}],
            max_tokens=1024,
        )

    (event,) = (item.payload for item in items)
    assert event["level"] == "error"


def test_span_status_error(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("event", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
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

    (error,) = (item.payload for item in items if item.type == "event")
    assert error["level"] == "error"

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["status"] == "error"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


@pytest.mark.asyncio
async def test_span_status_error_async(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("event", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
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

    (error,) = (item.payload for item in items if item.type == "event")
    assert error["level"] == "error"

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[0]["status"] == "error"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


@pytest.mark.asyncio
async def test_exception_message_create_async(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(
        side_effect=AnthropicError("API rate limit reached")
    )
    items = capture_items("event")

    with pytest.raises(AnthropicError):
        await client.messages.create(
            model="some-model",
            messages=[{"role": "system", "content": "I'm throwing an exception"}],
            max_tokens=1024,
        )

    (event,) = (item.payload for item in items)
    assert event["level"] == "error"


def test_span_origin(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
        client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.anthropic"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


@pytest.mark.asyncio
async def test_span_origin_async(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
        await client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert spans[1]["attributes"]["sentry.origin"] == "manual"
    assert spans[0]["attributes"]["sentry.origin"] == "auto.ai.anthropic"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert spans[0]["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"


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

    usage = _RecordedUsage()
    usage.output_tokens = 20
    usage.input_tokens = 10

    content_blocks = []

    model, new_usage, new_content_blocks, response_id, finish_reason = _collect_ai_data(
        event, model, usage, content_blocks
    )
    assert model is None
    assert new_usage.input_tokens == usage.input_tokens
    assert new_usage.output_tokens == usage.output_tokens
    assert new_content_blocks == ["test"]
    assert response_id is None
    assert finish_reason is None


@pytest.mark.skipif(
    ANTHROPIC_VERSION < (0, 27),
    reason="Versions <0.27.0 do not include InputJSONDelta.",
)
def test_set_output_data_with_input_json_delta(sentry_init):
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    span = sentry_sdk.traces.start_span(name="test")
    integration = AnthropicIntegration()
    json_deltas = ["{'test': 'data',", "'more': 'json'}"]
    _set_output_data(
        span,
        integration,
        model="",
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_write_input_tokens=0,
        content_blocks=[{"text": "".join(json_deltas), "type": "text"}],
    )

    assert (
        span._attributes.get(SPANDATA.GEN_AI_RESPONSE_TEXT)
        == "{'test': 'data','more': 'json'}"
    )
    assert span._attributes.get(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS) == 10
    assert span._attributes.get(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS) == 20
    assert span._attributes.get(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS) == 30


# Test messages with mixed roles including "ai" that should be mapped to "assistant"
@pytest.mark.parametrize(
    "test_message,expected_role",
    [
        ({"role": "system", "content": "You are helpful."}, "system"),
        ({"role": "user", "content": "Hello"}, "user"),
        (
            {"role": "ai", "content": "Hi there!"},
            "assistant",
        ),  # Should be mapped to "assistant"
        (
            {"role": "assistant", "content": "How can I help?"},
            "assistant",
        ),  # Should stay "assistant"
    ],
)
def test_anthropic_message_role_mapping(
    sentry_init,
    capture_items,
    test_message,
    expected_role,
):
    """Test that Anthropic integration properly maps message roles like 'ai' to 'assistant'"""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

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

    test_messages = [test_message]
    items = capture_items("span")

    client.messages.create(model="claude-3-opus", max_tokens=10, messages=test_messages)

    sentry_sdk.flush()
    span = next(item.payload for item in items)

    # Verify that the span was created correctly
    assert span["attributes"]["sentry.op"] == "gen_ai.chat"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]

    # Parse the stored messages
    stored_messages = json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    assert stored_messages[0]["role"] == expected_role


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    """Test that system prompts are properly captured in GEN_AI_REQUEST_MESSAGES."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")
    client.messages._post = mock.Mock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
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

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS in span["attributes"]
        system_instructions = json.loads(
            span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
        )
        assert system_instructions == [
            {"type": "text", "content": "You are a helpful assistant."}
        ]

        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        stored_messages = json.loads(
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert len(stored_messages) == 1
        assert stored_messages[0]["role"] == "user"
        assert stored_messages[0]["content"] == "Hello, Claude"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["end_turn"]


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
):
    """Test that system prompts are properly captured in GEN_AI_REQUEST_MESSAGES (async)."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    client = AsyncAnthropic(api_key="z")
    client.messages._post = AsyncMock(return_value=EXAMPLE_MESSAGE)

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with sentry_sdk.traces.start_span(name="anthropic"):
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

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS in span["attributes"]
        system_instructions = json.loads(
            span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
        )
        assert system_instructions == [
            {"type": "text", "content": "You are a helpful assistant."}
        ]

        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        stored_messages = json.loads(
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert len(stored_messages) == 1
        assert stored_messages[0]["role"] == "user"
        assert stored_messages[0]["content"] == "Hello, Claude"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi, I'm Claude."
    else:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is False
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == ["end_turn"]


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    """Test that system prompts are properly captured in streaming mode."""
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = client.messages.create(
            max_tokens=1024,
            messages=messages,
            model="model",
            stream=True,
            system="You are a helpful assistant.",
        )

        for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS in span["attributes"]
        system_instructions = json.loads(
            span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
        )
        assert system_instructions == [
            {"type": "text", "content": "You are a helpful assistant."}
        ]

        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        stored_messages = json.loads(
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert len(stored_messages) == 1
        assert stored_messages[0]["role"] == "user"
        assert stored_messages[0]["content"] == "Hello, Claude"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_stream_messages_with_system_prompt(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    server_side_event_chunks,
):
    """Test that system prompts are properly captured in streaming mode."""
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
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
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"), client.messages.stream(
        max_tokens=1024,
        messages=messages,
        model="model",
        system="You are a helpful assistant.",
    ) as stream:
        for event in stream:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS in span["attributes"]
        system_instructions = json.loads(
            span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
        )
        assert system_instructions == [
            {"type": "text", "content": "You are a helpful assistant."}
        ]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        stored_messages = json.loads(
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert len(stored_messages) == 1
        assert stored_messages[0]["role"] == "user"
        assert stored_messages[0]["content"] == "Hello, Claude"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"
    else:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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
async def test_stream_message_with_system_prompt_async(
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    """Test that system prompts are properly captured in streaming mode (async)."""
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        async with client.messages.stream(
            max_tokens=1024,
            messages=messages,
            model="model",
            system="You are a helpful assistant.",
        ) as stream:
            async for event in stream:
                pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS in span["attributes"]
        system_instructions = json.loads(
            span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
        )
        assert system_instructions == [
            {"type": "text", "content": "You are a helpful assistant."}
        ]

        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        stored_messages = json.loads(
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )
        assert len(stored_messages) == 1
        assert stored_messages[0]["role"] == "user"
        assert stored_messages[0]["content"] == "Hello, Claude"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"
    else:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


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
    sentry_init,
    capture_items,
    send_default_pii,
    include_prompts,
    get_model_response,
    async_iterator,
    server_side_event_chunks,
):
    """Test that system prompts are properly captured in streaming mode (async)."""
    client = AsyncAnthropic(api_key="z")

    response = get_model_response(
        async_iterator(
            server_side_event_chunks(
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
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=include_prompts)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    messages = [
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ]
    items = capture_items("transaction", "span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ) as _, sentry_sdk.traces.start_span(name="anthropic"):
        message = await client.messages.create(
            max_tokens=1024,
            messages=messages,
            model="model",
            stream=True,
            system="You are a helpful assistant.",
        )

        async for _ in message:
            pass

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]

    assert spans[1]["name"] == "anthropic"
    assert len(spans) == 2
    (span, _) = spans

    assert span["attributes"]["sentry.op"] == OP.GEN_AI_CHAT
    assert span["name"] == "chat model"
    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "model"

    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS in span["attributes"]
        system_instructions = json.loads(
            span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
        )
        assert system_instructions == [
            {"type": "text", "content": "You are a helpful assistant."}
        ]

        assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
        stored_messages = json.loads(
            span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )

        assert len(stored_messages) == 1
        assert stored_messages[0]["role"] == "user"
        assert stored_messages[0]["content"] == "Hello, Claude"
        assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_TEXT] == "Hi! I'm Claude!"

    else:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in span["attributes"]
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in span["attributes"]

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


def test_system_prompt_with_complex_structure(
    sentry_init,
    capture_items,
):
    """Test that complex system prompt structures (list of text blocks) are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

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
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="anthropic"):
        response = client.messages.create(
            max_tokens=1024, messages=messages, model="model", system=system_prompt
        )

    assert response == EXAMPLE_MESSAGE

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2

    assert spans[1]["name"] == "anthropic"
    (span, _) = spans

    assert span["attributes"][SPANDATA.GEN_AI_SYSTEM] == "anthropic"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"

    assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS in span["attributes"]
    system_instructions = json.loads(
        span["attributes"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
    )

    # System content should be a list of text blocks
    assert isinstance(system_instructions, list)
    assert system_instructions == [
        {"type": "text", "content": "You are a helpful assistant."},
        {"type": "text", "content": "Be concise and clear."},
    ]

    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in span["attributes"]
    stored_messages = json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    assert len(stored_messages) == 1
    assert stored_messages[0]["role"] == "user"
    assert stored_messages[0]["content"] == "Hello"


# Tests for transform_content_part (shared) and _transform_anthropic_content_block helper functions


def test_transform_content_part_anthropic_base64_image():
    """Test that base64 encoded images are transformed to blob format."""
    content_block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "base64encodeddata...",
        },
    }

    result = transform_content_part(content_block)

    assert result == {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/jpeg",
        "content": "base64encodeddata...",
    }


def test_transform_content_part_anthropic_url_image():
    """Test that URL-referenced images are transformed to uri format."""
    content_block = {
        "type": "image",
        "source": {
            "type": "url",
            "url": "https://example.com/image.jpg",
        },
    }

    result = transform_content_part(content_block)

    assert result == {
        "type": "uri",
        "modality": "image",
        "mime_type": "",
        "uri": "https://example.com/image.jpg",
    }


def test_transform_content_part_anthropic_file_image():
    """Test that file_id-referenced images are transformed to file format."""
    content_block = {
        "type": "image",
        "source": {
            "type": "file",
            "file_id": "file_abc123",
        },
    }

    result = transform_content_part(content_block)

    assert result == {
        "type": "file",
        "modality": "image",
        "mime_type": "",
        "file_id": "file_abc123",
    }


def test_transform_content_part_anthropic_base64_document():
    """Test that base64 encoded PDFs are transformed to blob format."""
    content_block = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "base64encodedpdfdata...",
        },
    }

    result = transform_content_part(content_block)

    assert result == {
        "type": "blob",
        "modality": "document",
        "mime_type": "application/pdf",
        "content": "base64encodedpdfdata...",
    }


def test_transform_content_part_anthropic_url_document():
    """Test that URL-referenced documents are transformed to uri format."""
    content_block = {
        "type": "document",
        "source": {
            "type": "url",
            "url": "https://example.com/document.pdf",
        },
    }

    result = transform_content_part(content_block)

    assert result == {
        "type": "uri",
        "modality": "document",
        "mime_type": "",
        "uri": "https://example.com/document.pdf",
    }


def test_transform_content_part_anthropic_file_document():
    """Test that file_id-referenced documents are transformed to file format."""
    content_block = {
        "type": "document",
        "source": {
            "type": "file",
            "file_id": "file_doc456",
            "media_type": "application/pdf",
        },
    }

    result = transform_content_part(content_block)

    assert result == {
        "type": "file",
        "modality": "document",
        "mime_type": "application/pdf",
        "file_id": "file_doc456",
    }


def test_transform_anthropic_content_block_text_document():
    """Test that plain text documents are transformed correctly (Anthropic-specific)."""
    content_block = {
        "type": "document",
        "source": {
            "type": "text",
            "media_type": "text/plain",
            "data": "This is plain text content.",
        },
    }

    # Use Anthropic-specific helper for text-type documents
    result = _transform_anthropic_content_block(content_block)

    assert result == {
        "type": "text",
        "text": "This is plain text content.",
    }


def test_transform_content_part_text_block():
    """Test that regular text blocks return None (not transformed)."""
    content_block = {
        "type": "text",
        "text": "Hello, world!",
    }

    # Shared transform_content_part returns None for text blocks
    result = transform_content_part(content_block)

    assert result is None


def test_transform_message_content_string():
    """Test that string content is returned as-is."""
    result = transform_message_content("Hello, world!")
    assert result == "Hello, world!"


def test_transform_message_content_list_anthropic():
    """Test that list content with Anthropic format is transformed correctly."""
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

    result = transform_message_content(content)

    assert len(result) == 2
    # Text block stays as-is (transform returns None, keeps original)
    assert result[0] == {"type": "text", "text": "Hello!"}
    assert result[1] == {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/png",
        "content": "base64data...",
    }


# Integration tests for binary data in messages


def test_message_with_url_image(
    sentry_init,
    capture_items,
):
    """Test that messages with URL-referenced images are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

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
    items = capture_items("span")

    client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = spans

    stored_messages = json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "uri",
        "modality": "image",
        "mime_type": "",
        "uri": "https://example.com/photo.png",
    }


def test_message_with_file_image(
    sentry_init,
    capture_items,
):
    """Test that messages with file_id-referenced images are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

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
    items = capture_items("span")

    client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = spans

    stored_messages = json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "file",
        "modality": "image",
        "mime_type": "image/webp",
        "file_id": "file_img_12345",
    }


def test_message_with_url_pdf(
    sentry_init,
    capture_items,
):
    """Test that messages with URL-referenced PDF documents are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

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
    items = capture_items("span")

    client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = spans

    stored_messages = json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "uri",
        "modality": "document",
        "mime_type": "",
        "uri": "https://example.com/report.pdf",
    }


def test_message_with_file_document(
    sentry_init,
    capture_items,
):
    """Test that messages with file_id-referenced documents are properly captured."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

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
    items = capture_items("span")

    client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = spans

    stored_messages = json.loads(span["attributes"][SPANDATA.GEN_AI_REQUEST_MESSAGES])

    content = stored_messages[0]["content"]
    assert content[1] == {
        "type": "file",
        "modality": "document",
        "mime_type": "application/pdf",
        "file_id": "file_doc_67890",
    }


def test_binary_content_not_stored_when_pii_disabled(
    sentry_init,
    capture_items,
):
    """Test that binary content is not stored when send_default_pii is False."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=True)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=False,
        trace_lifecycle="stream",
    )

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
    items = capture_items("span")

    client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = spans

    # Messages should not be stored
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]


def test_binary_content_not_stored_when_prompts_disabled(
    sentry_init,
    capture_items,
):
    """Test that binary content is not stored when include_prompts is False."""
    sentry_init(
        integrations=[AnthropicIntegration(include_prompts=False)],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

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
    items = capture_items("span")

    client.messages.create(max_tokens=1024, messages=messages, model="model")

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = spans

    # Messages should not be stored
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in span["attributes"]


def test_cache_tokens_nonstreaming(
    sentry_init,
    capture_items,
):
    """Test cache read/write tokens are tracked for non-streaming responses."""
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")

    client.messages._post = mock.Mock(
        return_value=Message(
            id="id",
            model="claude-3-5-sonnet-20241022",
            role="assistant",
            content=[TextBlock(type="text", text="Response")],
            type="message",
            usage=Usage(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=80,
                cache_creation_input_tokens=20,
            ),
        )
    )
    items = capture_items("span")

    client.messages.create(
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hello"}],
        model="claude-3-5-sonnet-20241022",
    )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    # input_tokens normalized: 100 + 80 (cache_read) + 20 (cache_write) = 200
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 200
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 50
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 250
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20


def test_input_tokens_include_cache_write_nonstreaming(
    sentry_init,
    capture_items,
):
    """
    Test that gen_ai.usage.input_tokens includes cache_write tokens (non-streaming).

    Reproduces a real Anthropic cache-write response. Anthropic's usage.input_tokens
    only counts non-cached tokens, but gen_ai.usage.input_tokens should be the TOTAL
    so downstream cost calculations don't produce negative values.

    Real Anthropic response (from E2E test):
        Usage(input_tokens=19, output_tokens=14,
              cache_creation_input_tokens=2846, cache_read_input_tokens=0)
    """
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")

    client.messages._post = mock.Mock(
        return_value=Message(
            id="id",
            model="claude-sonnet-4-20250514",
            role="assistant",
            content=[TextBlock(type="text", text="3 + 3 equals 6.")],
            type="message",
            usage=Usage(
                input_tokens=19,
                output_tokens=14,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=2846,
            ),
        )
    )
    items = capture_items("span")

    client.messages.create(
        max_tokens=1024,
        messages=[{"role": "user", "content": "What is 3+3?"}],
        model="claude-sonnet-4-20250514",
    )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    # input_tokens should be total: 19 (non-cached) + 2846 (cache_write) = 2865
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 2865
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 2879  # 2865 + 14
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 0
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 2846


def test_input_tokens_include_cache_read_nonstreaming(
    sentry_init,
    capture_items,
):
    """
    Test that gen_ai.usage.input_tokens includes cache_read tokens (non-streaming).

    Reproduces a real Anthropic cache-hit response. This is the scenario that
    caused negative gen_ai.cost.input_tokens: input_tokens=19 but cached=2846,
    so the backend computed 19 - 2846 = -2827 "regular" tokens.

    Real Anthropic response (from E2E test):
        Usage(input_tokens=19, output_tokens=14,
              cache_creation_input_tokens=0, cache_read_input_tokens=2846)
    """
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")

    client.messages._post = mock.Mock(
        return_value=Message(
            id="id",
            model="claude-sonnet-4-20250514",
            role="assistant",
            content=[TextBlock(type="text", text="5 + 5 = 10.")],
            type="message",
            usage=Usage(
                input_tokens=19,
                output_tokens=14,
                cache_read_input_tokens=2846,
                cache_creation_input_tokens=0,
            ),
        )
    )
    items = capture_items("span")

    client.messages.create(
        max_tokens=1024,
        messages=[{"role": "user", "content": "What is 5+5?"}],
        model="claude-sonnet-4-20250514",
    )

    sentry_sdk.flush()
    (span,) = [item.payload for item in items]

    # input_tokens should be total: 19 (non-cached) + 2846 (cache_read) = 2865
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 2865
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 2879  # 2865 + 14
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 2846
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 0


def test_input_tokens_include_cache_read_streaming(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """
    Test that gen_ai.usage.input_tokens includes cache_read tokens (streaming).

    Same cache-hit scenario as non-streaming, using realistic streaming events.
    """
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
            [
                MessageStartEvent(
                    type="message_start",
                    message=Message(
                        id="id",
                        model="claude-sonnet-4-20250514",
                        role="assistant",
                        content=[],
                        type="message",
                        usage=Usage(
                            input_tokens=19,
                            output_tokens=0,
                            cache_read_input_tokens=2846,
                            cache_creation_input_tokens=0,
                        ),
                    ),
                ),
                MessageDeltaEvent(
                    type="message_delta",
                    delta=Delta(stop_reason="end_turn"),
                    usage=MessageDeltaUsage(output_tokens=14),
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ):
        for _ in client.messages.create(
            max_tokens=1024,
            messages=[{"role": "user", "content": "What is 5+5?"}],
            model="claude-sonnet-4-20250514",
            stream=True,
        ):
            pass

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    # input_tokens should be total: 19 + 2846 = test_stream_messages_input_tokens_include_cache_read_streaming
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 2865
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 2879  # 2865 + 14
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 2846
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 0


def test_stream_messages_input_tokens_include_cache_read_streaming(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """
    Test that gen_ai.usage.input_tokens includes cache_read tokens (streaming).

    Same cache-hit scenario as non-streaming, using realistic streaming events.
    """
    client = Anthropic(api_key="z")
    response = get_model_response(
        server_side_event_chunks(
            [
                MessageStartEvent(
                    type="message_start",
                    message=Message(
                        id="id",
                        model="claude-sonnet-4-20250514",
                        role="assistant",
                        content=[],
                        type="message",
                        usage=Usage(
                            input_tokens=19,
                            output_tokens=0,
                            cache_read_input_tokens=2846,
                            cache_creation_input_tokens=0,
                        ),
                    ),
                ),
                MessageDeltaEvent(
                    type="message_delta",
                    delta=Delta(stop_reason="end_turn"),
                    usage=MessageDeltaUsage(output_tokens=14),
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ), client.messages.stream(
        max_tokens=1024,
        messages=[{"role": "user", "content": "What is 5+5?"}],
        model="claude-sonnet-4-20250514",
    ) as stream:
        for event in stream:
            pass

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    # input_tokens should be total: 19 + 2846 = 2865
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 2865
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 2879  # 2865 + 14
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 2846
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 0


def test_input_tokens_unchanged_without_caching(
    sentry_init,
    capture_items,
):
    """
    Test that input_tokens is unchanged when there are no cached tokens.

    Real Anthropic response (from E2E test, simple call without caching):
        Usage(input_tokens=20, output_tokens=12)
    """
    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    client = Anthropic(api_key="z")

    client.messages._post = mock.Mock(
        return_value=Message(
            id="id",
            model="claude-sonnet-4-20250514",
            role="assistant",
            content=[TextBlock(type="text", text="2+2 equals 4.")],
            type="message",
            usage=Usage(
                input_tokens=20,
                output_tokens=12,
            ),
        )
    )
    items = capture_items("span")

    client.messages.create(
        max_tokens=1024,
        messages=[{"role": "user", "content": "What is 2+2?"}],
        model="claude-sonnet-4-20250514",
    )

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)

    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 20
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 32  # 20 + 12


def test_cache_tokens_streaming(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """Test cache tokens are tracked for streaming responses."""
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
            [
                MessageStartEvent(
                    type="message_start",
                    message=Message(
                        id="id",
                        model="claude-3-5-sonnet-20241022",
                        role="assistant",
                        content=[],
                        type="message",
                        usage=Usage(
                            input_tokens=100,
                            output_tokens=0,
                            cache_read_input_tokens=80,
                            cache_creation_input_tokens=20,
                        ),
                    ),
                ),
                MessageDeltaEvent(
                    type="message_delta",
                    delta=Delta(stop_reason="end_turn"),
                    usage=MessageDeltaUsage(output_tokens=10),
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ):
        for _ in client.messages.create(
            max_tokens=1024,
            messages=[{"role": "user", "content": "Hello"}],
            model="claude-3-5-sonnet-20241022",
            stream=True,
        ):
            pass

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    # input_tokens normalized: 100 + 80 (cache_read) + 20 (cache_write) = 200
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 200
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 210
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20


def test_stream_messages_cache_tokens(
    sentry_init,
    capture_items,
    get_model_response,
    server_side_event_chunks,
):
    """Test cache tokens are tracked for streaming responses."""
    client = Anthropic(api_key="z")

    response = get_model_response(
        server_side_event_chunks(
            [
                MessageStartEvent(
                    type="message_start",
                    message=Message(
                        id="id",
                        model="claude-3-5-sonnet-20241022",
                        role="assistant",
                        content=[],
                        type="message",
                        usage=Usage(
                            input_tokens=100,
                            output_tokens=0,
                            cache_read_input_tokens=80,
                            cache_creation_input_tokens=20,
                        ),
                    ),
                ),
                MessageDeltaEvent(
                    type="message_delta",
                    delta=Delta(stop_reason="end_turn"),
                    usage=MessageDeltaUsage(output_tokens=10),
                ),
            ]
        )
    )

    sentry_init(
        integrations=[AnthropicIntegration()],
        disabled_integrations=[StdlibIntegration],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with mock.patch.object(
        client._client,
        "send",
        return_value=response,
    ), client.messages.stream(
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hello"}],
        model="claude-3-5-sonnet-20241022",
    ) as stream:
        for event in stream:
            pass

    sentry_sdk.flush()
    (span,) = (item.payload for item in items)
    # input_tokens normalized: 100 + 80 (cache_read) + 20 (cache_write) = 200
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 200
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 210
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 80
    assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] == 20

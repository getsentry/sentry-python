import pytest
from unittest import mock
import json

from anthropic import Anthropic
from anthropic.types.message import Message
from anthropic.types.usage import Usage

try:
    from anthropic.types.text_block import TextBlock
except ImportError:
    from anthropic.types.content_block import ContentBlock as TextBlock

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.anthropic import AnthropicIntegration


EXAMPLE_MESSAGE = Message(
    id="msg_01XFDUDYJgAACzvnptvVoYEL",
    model="model",
    role="assistant",
    content=[TextBlock(type="text", text="Hi, I'm Claude.")],
    type="message",
    stop_reason="end_turn",
    usage=Usage(input_tokens=10, output_tokens=20),
)

TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather in a given location",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city and state, e.g. San Francisco, CA",
                },
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["location"],
        },
    },
    {
        "name": "no_description_tool",
        "input_schema": {
            "type": "object",
            "properties": {"arg1": {"type": "string"}},
        },
    },
]


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_tool_definitions_in_create_message(
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
            "content": "What is the weather in San Francisco?",
        }
    ]

    with start_transaction(name="anthropic"):
        client.messages.create(
            max_tokens=1024,
            messages=messages,
            model="model",
            tools=TOOLS,
        )

    assert len(events) == 1
    (event,) = events
    (span,) = event["spans"]

    assert span["op"] == OP.GEN_AI_CHAT

    # Check old available_tools attribute (always present if tools provided)
    assert SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS in span["data"]
    available_tools = json.loads(span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS])
    assert available_tools == TOOLS

    # Check new tool.definitions attribute (only present if PII and prompts enabled)
    if send_default_pii and include_prompts:
        assert SPANDATA.GEN_AI_TOOL_DEFINITIONS in span["data"]
        tool_definitions = json.loads(span["data"][SPANDATA.GEN_AI_TOOL_DEFINITIONS])
        assert len(tool_definitions) == 2

        # Check tool with description
        assert tool_definitions[0]["name"] == "get_weather"
        assert (
            tool_definitions[0]["description"]
            == "Get the current weather in a given location"
        )
        assert tool_definitions[0]["type"] == "function"
        assert tool_definitions[0]["parameters"] == TOOLS[0]["input_schema"]

        # Check tool without description
        assert tool_definitions[1]["name"] == "no_description_tool"
        assert "description" not in tool_definitions[1]
        assert tool_definitions[1]["type"] == "function"
        assert tool_definitions[1]["parameters"] == TOOLS[1]["input_schema"]
    else:
        assert SPANDATA.GEN_AI_TOOL_DEFINITIONS not in span["data"]

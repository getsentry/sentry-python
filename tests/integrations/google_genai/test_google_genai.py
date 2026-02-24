import json
import pytest
from unittest import mock

from google import genai
from google.genai import types as genai_types
from google.genai.types import Content, Part

from sentry_sdk import start_transaction
from sentry_sdk._types import BLOB_DATA_SUBSTITUTE
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration
from sentry_sdk.integrations.google_genai.utils import extract_contents_messages


@pytest.fixture
def mock_genai_client():
    """Fixture that creates a real genai.Client with mocked HTTP responses."""
    client = genai.Client(api_key="test-api-key")
    return client


def create_mock_http_response(response_body):
    """
    Create a mock HTTP response that the API client's request() method would return.

    Args:
        response_body: The JSON body as a string or dict

    Returns:
        An HttpResponse object with headers and body
    """
    if isinstance(response_body, dict):
        response_body = json.dumps(response_body)

    return genai_types.HttpResponse(
        headers={
            "content-type": "application/json; charset=UTF-8",
        },
        body=response_body,
    )


def create_mock_streaming_responses(response_chunks):
    """
    Create a generator that yields mock HTTP responses for streaming.

    Args:
        response_chunks: List of dicts, each representing a chunk's JSON body

    Returns:
        A generator that yields HttpResponse objects
    """
    for chunk in response_chunks:
        yield create_mock_http_response(chunk)


# Sample API response JSON (based on real API format from user)
EXAMPLE_API_RESPONSE_JSON = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"text": "Hello! How can I help you today?"}],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 10,
        "candidatesTokenCount": 20,
        "totalTokenCount": 30,
        "cachedContentTokenCount": 5,
        "thoughtsTokenCount": 3,
    },
    "modelVersion": "gemini-1.5-flash",
    "responseId": "response-id-123",
}


def create_test_config(
    temperature=None,
    top_p=None,
    top_k=None,
    max_output_tokens=None,
    presence_penalty=None,
    frequency_penalty=None,
    seed=None,
    system_instruction=None,
    tools=None,
):
    """Create a GenerateContentConfig."""
    config_dict = {}

    if temperature is not None:
        config_dict["temperature"] = temperature
    if top_p is not None:
        config_dict["top_p"] = top_p
    if top_k is not None:
        config_dict["top_k"] = top_k
    if max_output_tokens is not None:
        config_dict["max_output_tokens"] = max_output_tokens
    if presence_penalty is not None:
        config_dict["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        config_dict["frequency_penalty"] = frequency_penalty
    if seed is not None:
        config_dict["seed"] = seed
    if system_instruction is not None:
        config_dict["system_instruction"] = system_instruction
    if tools is not None:
        config_dict["tools"] = tools

    return genai_types.GenerateContentConfig(**config_dict)


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_nonstreaming_generate_content(
    sentry_init, capture_events, send_default_pii, include_prompts, mock_genai_client
):
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    # Mock the HTTP response at the _api_client.request() level
    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client,
        "request",
        return_value=mock_http_response,
    ):
        with start_transaction(name="google_genai"):
            config = create_test_config(temperature=0.7, max_output_tokens=100)
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="Tell me a joke", config=config
            )
    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert event["transaction"] == "google_genai"

    assert len(event["spans"]) == 1
    chat_span = event["spans"][0]

    # Check chat span
    assert chat_span["op"] == OP.GEN_AI_CHAT
    assert chat_span["description"] == "chat gemini-1.5-flash"
    assert chat_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert chat_span["data"][SPANDATA.GEN_AI_SYSTEM] == "gcp.gemini"
    assert chat_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gemini-1.5-flash"

    if send_default_pii and include_prompts:
        # Response text is stored as a JSON array
        response_text = chat_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        # Parse the JSON array
        response_texts = json.loads(response_text)
        assert response_texts == ["Hello! How can I help you today?"]
    else:
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_span["data"]

    # Check token usage
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    # Output tokens now include reasoning tokens: candidates_token_count (20) + thoughts_token_count (3) = 23
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 23
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 5
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING] == 3


@pytest.mark.parametrize("generate_content_config", (False, True))
@pytest.mark.parametrize(
    "system_instructions,expected_texts",
    [
        (None, None),
        ({}, []),
        (Content(role="system", parts=[]), []),
        ({"parts": []}, []),
        ("You are a helpful assistant.", ["You are a helpful assistant."]),
        (Part(text="You are a helpful assistant."), ["You are a helpful assistant."]),
        (
            Content(role="system", parts=[Part(text="You are a helpful assistant.")]),
            ["You are a helpful assistant."],
        ),
        ({"text": "You are a helpful assistant."}, ["You are a helpful assistant."]),
        (
            {"parts": [Part(text="You are a helpful assistant.")]},
            ["You are a helpful assistant."],
        ),
        (
            {"parts": [{"text": "You are a helpful assistant."}]},
            ["You are a helpful assistant."],
        ),
        (["You are a helpful assistant."], ["You are a helpful assistant."]),
        ([Part(text="You are a helpful assistant.")], ["You are a helpful assistant."]),
        ([{"text": "You are a helpful assistant."}], ["You are a helpful assistant."]),
    ],
)
def test_generate_content_with_system_instruction(
    sentry_init,
    capture_events,
    mock_genai_client,
    generate_content_config,
    system_instructions,
    expected_texts,
):
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            config = {
                "system_instruction": system_instructions,
                "temperature": 0.5,
            }

            if generate_content_config:
                config = create_test_config(**config)

            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash",
                contents="What is 2+2?",
                config=config,
            )

    (event,) = events
    invoke_span = event["spans"][0]

    if expected_texts is None:
        assert SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS not in invoke_span["data"]
        return

    # (PII is enabled and include_prompts is True in this test)
    system_instructions = json.loads(
        invoke_span["data"][SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS]
    )

    assert system_instructions == [
        {"type": "text", "content": text} for text in expected_texts
    ]


def test_generate_content_with_tools(sentry_init, capture_events, mock_genai_client):
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Create a mock tool function
    def get_weather(location: str) -> str:
        """Get the weather for a location"""
        return f"The weather in {location} is sunny"

    # Create a tool with function declarations using real types
    function_declaration = genai_types.FunctionDeclaration(
        name="get_weather_tool",
        description="Get weather information (tool object)",
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "location": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="The location to get weather for",
                )
            },
            required=["location"],
        ),
    )

    mock_tool = genai_types.Tool(function_declarations=[function_declaration])

    # API response for tool usage
    tool_response_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "I'll check the weather."}],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 15,
            "candidatesTokenCount": 10,
            "totalTokenCount": 25,
        },
    }

    mock_http_response = create_mock_http_response(tool_response_json)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            config = create_test_config(tools=[get_weather, mock_tool])
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="What's the weather?", config=config
            )

    (event,) = events
    invoke_span = event["spans"][0]

    # Check that tools are recorded (data is serialized as a string)
    tools_data_str = invoke_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
    # Parse the JSON string to verify content
    tools_data = json.loads(tools_data_str)
    assert len(tools_data) == 2

    # The order of tools may not be guaranteed, so sort by name and description for comparison
    sorted_tools = sorted(
        tools_data, key=lambda t: (t.get("name", ""), t.get("description", ""))
    )

    # The function tool
    assert sorted_tools[0]["name"] == "get_weather"
    assert sorted_tools[0]["description"] == "Get the weather for a location"

    # The FunctionDeclaration tool
    assert sorted_tools[1]["name"] == "get_weather_tool"
    assert sorted_tools[1]["description"] == "Get weather information (tool object)"


def test_tool_execution(sentry_init, capture_events):
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Create a mock tool function
    def get_weather(location: str) -> str:
        """Get the weather for a location"""
        return f"The weather in {location} is sunny"

    # Create wrapped version of the tool
    from sentry_sdk.integrations.google_genai.utils import wrapped_tool

    wrapped_weather = wrapped_tool(get_weather)

    # Execute the wrapped tool
    with start_transaction(name="test_tool"):
        result = wrapped_weather("San Francisco")

    assert result == "The weather in San Francisco is sunny"

    (event,) = events
    assert len(event["spans"]) == 1
    tool_span = event["spans"][0]

    assert tool_span["op"] == OP.GEN_AI_EXECUTE_TOOL
    assert tool_span["description"] == "execute_tool get_weather"
    assert tool_span["data"][SPANDATA.GEN_AI_TOOL_NAME] == "get_weather"
    assert tool_span["data"][SPANDATA.GEN_AI_TOOL_TYPE] == "function"
    assert (
        tool_span["data"][SPANDATA.GEN_AI_TOOL_DESCRIPTION]
        == "Get the weather for a location"
    )


def test_error_handling(sentry_init, capture_events, mock_genai_client):
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Mock an error at the HTTP level
    with mock.patch.object(
        mock_genai_client._api_client, "request", side_effect=Exception("API Error")
    ):
        with start_transaction(name="google_genai"):
            with pytest.raises(Exception, match="API Error"):
                mock_genai_client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents="This will fail",
                    config=create_test_config(),
                )

    # Should have both transaction and error events
    assert len(events) == 2
    error_event, transaction_event = events

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert error_event["exception"]["values"][0]["value"] == "API Error"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "google_genai"


def test_streaming_generate_content(sentry_init, capture_events, mock_genai_client):
    """Test streaming with generate_content_stream, verifying chunk accumulation."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Create streaming chunks - simulating a multi-chunk response
    # Chunk 1: First part of text with partial usage metadata
    chunk1_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "Hello! "}],
                },
                # No finishReason in intermediate chunks
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 2,
            "totalTokenCount": 12,
        },
        "responseId": "response-id-stream-123",
        "modelVersion": "gemini-1.5-flash",
    }

    # Chunk 2: Second part of text with intermediate usage metadata
    chunk2_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "How can I "}],
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 3,
            "totalTokenCount": 13,
        },
    }

    # Chunk 3: Final part with finish reason and complete usage metadata
    chunk3_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "help you today?"}],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 7,
            "totalTokenCount": 25,
            "cachedContentTokenCount": 5,
            "thoughtsTokenCount": 3,
        },
    }

    # Create streaming mock responses
    stream_chunks = [chunk1_json, chunk2_json, chunk3_json]
    mock_stream = create_mock_streaming_responses(stream_chunks)

    with mock.patch.object(
        mock_genai_client._api_client, "request_streamed", return_value=mock_stream
    ):
        with start_transaction(name="google_genai"):
            config = create_test_config()
            stream = mock_genai_client.models.generate_content_stream(
                model="gemini-1.5-flash", contents="Stream me a response", config=config
            )

            # Consume the stream (this is what users do with the integration wrapper)
            collected_chunks = list(stream)

    # Verify we got all chunks
    assert len(collected_chunks) == 3
    assert collected_chunks[0].candidates[0].content.parts[0].text == "Hello! "
    assert collected_chunks[1].candidates[0].content.parts[0].text == "How can I "
    assert collected_chunks[2].candidates[0].content.parts[0].text == "help you today?"

    (event,) = events

    assert len(event["spans"]) == 1
    chat_span = event["spans"][0]

    # Check that streaming flag is set on both spans
    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    # Verify accumulated response text (all chunks combined)
    expected_full_text = "Hello! How can I help you today?"
    # Response text is stored as a JSON string
    chat_response_text = json.loads(chat_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT])
    assert chat_response_text == [expected_full_text]

    # Verify finish reasons (only the final chunk has a finish reason)
    # When there's a single finish reason, it's stored as a plain string (not JSON)
    assert SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS in chat_span["data"]
    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == "STOP"
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 10
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 25
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 5
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING] == 3

    # Verify model name
    assert chat_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gemini-1.5-flash"


def test_span_origin(sentry_init, capture_events, mock_genai_client):
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            config = create_test_config()
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="Test origin", config=config
            )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    for span in event["spans"]:
        assert span["origin"] == "auto.ai.google_genai"


def test_response_without_usage_metadata(
    sentry_init, capture_events, mock_genai_client
):
    """Test handling of responses without usage metadata"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Response without usage metadata
    response_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "No usage data"}],
                },
                "finishReason": "STOP",
            }
        ],
    }

    mock_http_response = create_mock_http_response(response_json)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            config = create_test_config()
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="Test", config=config
            )

    (event,) = events
    chat_span = event["spans"][0]

    # Usage data should not be present
    assert SPANDATA.GEN_AI_USAGE_INPUT_TOKENS not in chat_span["data"]
    assert SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS not in chat_span["data"]
    assert SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS not in chat_span["data"]


def test_multiple_candidates(sentry_init, capture_events, mock_genai_client):
    """Test handling of multiple response candidates"""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Response with multiple candidates
    multi_candidate_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "Response 1"}],
                },
                "finishReason": "STOP",
            },
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "Response 2"}],
                },
                "finishReason": "MAX_TOKENS",
            },
        ],
        "usageMetadata": {
            "promptTokenCount": 5,
            "candidatesTokenCount": 15,
            "totalTokenCount": 20,
        },
    }

    mock_http_response = create_mock_http_response(multi_candidate_json)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            config = create_test_config()
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="Generate multiple", config=config
            )

    (event,) = events
    chat_span = event["spans"][0]

    # Should capture all responses
    # Response text is stored as a JSON string when there are multiple responses
    response_text = chat_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    if isinstance(response_text, str) and response_text.startswith("["):
        # It's a JSON array
        response_list = json.loads(response_text)
        assert response_list == ["Response 1", "Response 2"]
    else:
        # It's concatenated
        assert response_text == "Response 1\nResponse 2"

    # Finish reasons are serialized as JSON
    finish_reasons = json.loads(
        chat_span["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS]
    )
    assert finish_reasons == ["STOP", "MAX_TOKENS"]


def test_all_configuration_parameters(sentry_init, capture_events, mock_genai_client):
    """Test that all configuration parameters are properly recorded"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            config = create_test_config(
                temperature=0.8,
                top_p=0.95,
                top_k=40,
                max_output_tokens=2048,
                presence_penalty=0.1,
                frequency_penalty=0.2,
                seed=12345,
            )
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="Test all params", config=config
            )

    (event,) = events
    invoke_span = event["spans"][0]

    # Check all parameters are recorded
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.8
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_TOP_P] == 0.95
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_TOP_K] == 40
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 2048
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY] == 0.1
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY] == 0.2
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_SEED] == 12345


def test_empty_response(sentry_init, capture_events, mock_genai_client):
    """Test handling of minimal response with no content"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Minimal response with empty candidates array
    minimal_response_json = {"candidates": []}
    mock_http_response = create_mock_http_response(minimal_response_json)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            response = mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="Test", config=create_test_config()
            )

    # Response will have an empty candidates list
    assert response is not None
    assert len(response.candidates) == 0

    (event,) = events
    # Should still create spans even with empty candidates
    assert len(event["spans"]) == 1


def test_response_with_different_id_fields(
    sentry_init, capture_events, mock_genai_client
):
    """Test handling of different response ID field names"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Response with response_id and model_version
    response_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "Test"}],
                },
                "finishReason": "STOP",
            }
        ],
        "responseId": "resp-456",
        "modelVersion": "gemini-1.5-flash-001",
    }

    mock_http_response = create_mock_http_response(response_json)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="Test", config=create_test_config()
            )

    (event,) = events
    chat_span = event["spans"][0]

    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_ID] == "resp-456"
    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "gemini-1.5-flash-001"


def test_tool_with_async_function(sentry_init, capture_events):
    """Test that async tool functions are properly wrapped"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    capture_events()

    # Create an async tool function
    async def async_tool(param: str) -> str:
        """An async tool"""
        return f"Async result: {param}"

    # Import is skipped in sync tests, but we can test the wrapping logic
    from sentry_sdk.integrations.google_genai.utils import wrapped_tool

    # The wrapper should handle async functions
    wrapped_async_tool = wrapped_tool(async_tool)
    assert wrapped_async_tool != async_tool  # Should be wrapped
    assert hasattr(wrapped_async_tool, "__wrapped__")  # Should preserve original


def test_contents_as_none(sentry_init, capture_events, mock_genai_client):
    """Test handling when contents parameter is None"""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=None, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    # Should handle None contents gracefully
    messages = invoke_span["data"].get(SPANDATA.GEN_AI_REQUEST_MESSAGES, [])
    # Should only have system message if any, not user message
    assert all(msg["role"] != "user" or msg["content"] is not None for msg in messages)


def test_tool_calls_extraction(sentry_init, capture_events, mock_genai_client):
    """Test extraction of tool/function calls from response"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Response with function calls
    function_call_response_json = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "I'll help you with that."},
                        {
                            "functionCall": {
                                "name": "get_weather",
                                "args": {
                                    "location": "San Francisco",
                                    "unit": "celsius",
                                },
                            }
                        },
                        {
                            "functionCall": {
                                "name": "get_time",
                                "args": {"timezone": "PST"},
                            }
                        },
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 30,
            "totalTokenCount": 50,
        },
    }

    mock_http_response = create_mock_http_response(function_call_response_json)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash",
                contents="What's the weather and time?",
                config=create_test_config(),
            )

    (event,) = events
    chat_span = event["spans"][0]  # The chat span

    # Check that tool calls are extracted and stored
    assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_span["data"]

    # Parse the JSON string to verify content
    tool_calls = json.loads(chat_span["data"][SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS])

    assert len(tool_calls) == 2

    # First tool call
    assert tool_calls[0]["name"] == "get_weather"
    assert tool_calls[0]["type"] == "function_call"
    # Arguments are serialized as JSON strings
    assert json.loads(tool_calls[0]["arguments"]) == {
        "location": "San Francisco",
        "unit": "celsius",
    }

    # Second tool call
    assert tool_calls[1]["name"] == "get_time"
    assert tool_calls[1]["type"] == "function_call"
    # Arguments are serialized as JSON strings
    assert json.loads(tool_calls[1]["arguments"]) == {"timezone": "PST"}


def test_google_genai_message_truncation(
    sentry_init, capture_events, mock_genai_client
):
    """Test that large messages are truncated properly in Google GenAI integration."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    large_content = (
        "This is a very long message that will exceed our size limits. " * 1000
    )
    small_content = "This is a small user message"

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash",
                contents=[large_content, small_content],
                config=create_test_config(),
            )

    (event,) = events
    invoke_span = event["spans"][0]
    assert SPANDATA.GEN_AI_REQUEST_MESSAGES in invoke_span["data"]

    messages_data = invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    assert isinstance(messages_data, str)

    parsed_messages = json.loads(messages_data)
    assert isinstance(parsed_messages, list)
    assert len(parsed_messages) == 1
    assert parsed_messages[0]["role"] == "user"
    assert small_content in parsed_messages[0]["content"]

    assert (
        event["_meta"]["spans"]["0"]["data"]["gen_ai.request.messages"][""]["len"] == 2
    )


# Sample embed content API response JSON
EXAMPLE_EMBED_RESPONSE_JSON = {
    "embeddings": [
        {
            "values": [0.1, 0.2, 0.3, 0.4, 0.5],  # Simplified embedding vector
            "statistics": {
                "tokenCount": 10,
                "truncated": False,
            },
        },
        {
            "values": [0.2, 0.3, 0.4, 0.5, 0.6],
            "statistics": {
                "tokenCount": 15,
                "truncated": False,
            },
        },
    ],
    "metadata": {
        "billableCharacterCount": 42,
    },
}


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_embed_content(
    sentry_init, capture_events, send_default_pii, include_prompts, mock_genai_client
):
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    # Mock the HTTP response at the _api_client.request() level
    mock_http_response = create_mock_http_response(EXAMPLE_EMBED_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client,
        "request",
        return_value=mock_http_response,
    ):
        with start_transaction(name="google_genai_embeddings"):
            mock_genai_client.models.embed_content(
                model="text-embedding-004",
                contents=[
                    "What is your name?",
                    "What is your favorite color?",
                ],
            )

    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert event["transaction"] == "google_genai_embeddings"

    # Should have 1 span for embeddings
    assert len(event["spans"]) == 1
    (embed_span,) = event["spans"]

    # Check embeddings span
    assert embed_span["op"] == OP.GEN_AI_EMBEDDINGS
    assert embed_span["description"] == "embeddings text-embedding-004"
    assert embed_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
    assert embed_span["data"][SPANDATA.GEN_AI_SYSTEM] == "gcp.gemini"
    assert embed_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-004"

    # Check input texts if PII is allowed
    if send_default_pii and include_prompts:
        input_texts = json.loads(embed_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
        assert input_texts == [
            "What is your name?",
            "What is your favorite color?",
        ]
    else:
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embed_span["data"]

    # Check usage data (sum of token counts from statistics: 10 + 15 = 25)
    # Note: Only available in newer versions with ContentEmbeddingStatistics
    if SPANDATA.GEN_AI_USAGE_INPUT_TOKENS in embed_span["data"]:
        assert embed_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 25


def test_embed_content_string_input(sentry_init, capture_events, mock_genai_client):
    """Test embed_content with a single string instead of list."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Mock response with single embedding
    single_embed_response = {
        "embeddings": [
            {
                "values": [0.1, 0.2, 0.3],
                "statistics": {
                    "tokenCount": 5,
                    "truncated": False,
                },
            },
        ],
        "metadata": {
            "billableCharacterCount": 10,
        },
    }
    mock_http_response = create_mock_http_response(single_embed_response)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai_embeddings"):
            mock_genai_client.models.embed_content(
                model="text-embedding-004",
                contents="Single text input",
            )

    (event,) = events
    (embed_span,) = event["spans"]

    # Check that single string is handled correctly
    input_texts = json.loads(embed_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
    assert input_texts == ["Single text input"]
    # Should use token_count from statistics (5), not billable_character_count (10)
    # Note: Only available in newer versions with ContentEmbeddingStatistics
    if SPANDATA.GEN_AI_USAGE_INPUT_TOKENS in embed_span["data"]:
        assert embed_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5


def test_embed_content_error_handling(sentry_init, capture_events, mock_genai_client):
    """Test error handling in embed_content."""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Mock an error at the HTTP level
    with mock.patch.object(
        mock_genai_client._api_client,
        "request",
        side_effect=Exception("Embedding API Error"),
    ):
        with start_transaction(name="google_genai_embeddings"):
            with pytest.raises(Exception, match="Embedding API Error"):
                mock_genai_client.models.embed_content(
                    model="text-embedding-004",
                    contents=["This will fail"],
                )

    # Should have both transaction and error events
    assert len(events) == 2
    error_event, _ = events

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert error_event["exception"]["values"][0]["value"] == "Embedding API Error"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "google_genai"


def test_embed_content_without_statistics(
    sentry_init, capture_events, mock_genai_client
):
    """Test embed_content response without statistics (older package versions)."""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Response without statistics (typical for older google-genai versions)
    # Embeddings exist but don't have the statistics field
    old_version_response = {
        "embeddings": [
            {
                "values": [0.1, 0.2, 0.3],
            },
            {
                "values": [0.2, 0.3, 0.4],
            },
        ],
    }
    mock_http_response = create_mock_http_response(old_version_response)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai_embeddings"):
            mock_genai_client.models.embed_content(
                model="text-embedding-004",
                contents=["Test without statistics", "Another test"],
            )

    (event,) = events
    (embed_span,) = event["spans"]

    # No usage tokens since there are no statistics in older versions
    # This is expected and the integration should handle it gracefully
    assert SPANDATA.GEN_AI_USAGE_INPUT_TOKENS not in embed_span["data"]


def test_embed_content_span_origin(sentry_init, capture_events, mock_genai_client):
    """Test that embed_content spans have correct origin."""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_EMBED_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai_embeddings"):
            mock_genai_client.models.embed_content(
                model="text-embedding-004",
                contents=["Test origin"],
            )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    for span in event["spans"]:
        assert span["origin"] == "auto.ai.google_genai"


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
async def test_async_embed_content(
    sentry_init, capture_events, send_default_pii, include_prompts, mock_genai_client
):
    """Test async embed_content method."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    # Mock the async HTTP response
    mock_http_response = create_mock_http_response(EXAMPLE_EMBED_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client,
        "async_request",
        return_value=mock_http_response,
    ):
        with start_transaction(name="google_genai_embeddings_async"):
            await mock_genai_client.aio.models.embed_content(
                model="text-embedding-004",
                contents=[
                    "What is your name?",
                    "What is your favorite color?",
                ],
            )

    assert len(events) == 1
    (event,) = events

    assert event["type"] == "transaction"
    assert event["transaction"] == "google_genai_embeddings_async"

    # Should have 1 span for embeddings
    assert len(event["spans"]) == 1
    (embed_span,) = event["spans"]

    # Check embeddings span
    assert embed_span["op"] == OP.GEN_AI_EMBEDDINGS
    assert embed_span["description"] == "embeddings text-embedding-004"
    assert embed_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "embeddings"
    assert embed_span["data"][SPANDATA.GEN_AI_SYSTEM] == "gcp.gemini"
    assert embed_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "text-embedding-004"

    # Check input texts if PII is allowed
    if send_default_pii and include_prompts:
        input_texts = json.loads(embed_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
        assert input_texts == [
            "What is your name?",
            "What is your favorite color?",
        ]
    else:
        assert SPANDATA.GEN_AI_EMBEDDINGS_INPUT not in embed_span["data"]

    # Check usage data (sum of token counts from statistics: 10 + 15 = 25)
    # Note: Only available in newer versions with ContentEmbeddingStatistics
    if SPANDATA.GEN_AI_USAGE_INPUT_TOKENS in embed_span["data"]:
        assert embed_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 25


@pytest.mark.asyncio
async def test_async_embed_content_string_input(
    sentry_init, capture_events, mock_genai_client
):
    """Test async embed_content with a single string instead of list."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Mock response with single embedding
    single_embed_response = {
        "embeddings": [
            {
                "values": [0.1, 0.2, 0.3],
                "statistics": {
                    "tokenCount": 5,
                    "truncated": False,
                },
            },
        ],
        "metadata": {
            "billableCharacterCount": 10,
        },
    }
    mock_http_response = create_mock_http_response(single_embed_response)

    with mock.patch.object(
        mock_genai_client._api_client, "async_request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai_embeddings_async"):
            await mock_genai_client.aio.models.embed_content(
                model="text-embedding-004",
                contents="Single text input",
            )

    (event,) = events
    (embed_span,) = event["spans"]

    # Check that single string is handled correctly
    input_texts = json.loads(embed_span["data"][SPANDATA.GEN_AI_EMBEDDINGS_INPUT])
    assert input_texts == ["Single text input"]
    # Should use token_count from statistics (5), not billable_character_count (10)
    # Note: Only available in newer versions with ContentEmbeddingStatistics
    if SPANDATA.GEN_AI_USAGE_INPUT_TOKENS in embed_span["data"]:
        assert embed_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 5


@pytest.mark.asyncio
async def test_async_embed_content_error_handling(
    sentry_init, capture_events, mock_genai_client
):
    """Test error handling in async embed_content."""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Mock an error at the HTTP level
    with mock.patch.object(
        mock_genai_client._api_client,
        "async_request",
        side_effect=Exception("Async Embedding API Error"),
    ):
        with start_transaction(name="google_genai_embeddings_async"):
            with pytest.raises(Exception, match="Async Embedding API Error"):
                await mock_genai_client.aio.models.embed_content(
                    model="text-embedding-004",
                    contents=["This will fail"],
                )

    # Should have both transaction and error events
    assert len(events) == 2
    error_event, _ = events

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert error_event["exception"]["values"][0]["value"] == "Async Embedding API Error"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "google_genai"


@pytest.mark.asyncio
async def test_async_embed_content_without_statistics(
    sentry_init, capture_events, mock_genai_client
):
    """Test async embed_content response without statistics (older package versions)."""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Response without statistics (typical for older google-genai versions)
    # Embeddings exist but don't have the statistics field
    old_version_response = {
        "embeddings": [
            {
                "values": [0.1, 0.2, 0.3],
            },
            {
                "values": [0.2, 0.3, 0.4],
            },
        ],
    }
    mock_http_response = create_mock_http_response(old_version_response)

    with mock.patch.object(
        mock_genai_client._api_client, "async_request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai_embeddings_async"):
            await mock_genai_client.aio.models.embed_content(
                model="text-embedding-004",
                contents=["Test without statistics", "Another test"],
            )

    (event,) = events
    (embed_span,) = event["spans"]

    # No usage tokens since there are no statistics in older versions
    # This is expected and the integration should handle it gracefully
    assert SPANDATA.GEN_AI_USAGE_INPUT_TOKENS not in embed_span["data"]


@pytest.mark.asyncio
async def test_async_embed_content_span_origin(
    sentry_init, capture_events, mock_genai_client
):
    """Test that async embed_content spans have correct origin."""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_EMBED_RESPONSE_JSON)

    with mock.patch.object(
        mock_genai_client._api_client, "async_request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai_embeddings_async"):
            await mock_genai_client.aio.models.embed_content(
                model="text-embedding-004",
                contents=["Test origin"],
            )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    for span in event["spans"]:
        assert span["origin"] == "auto.ai.google_genai"


# Integration tests for generate_content with different input message formats
def test_generate_content_with_content_object(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with Content object input."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Create Content object
    content = genai_types.Content(
        role="user", parts=[genai_types.Part(text="Hello from Content object")]
    )

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=content, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == [
        {"text": "Hello from Content object", "type": "text"}
    ]


def test_generate_content_with_dict_format(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with dict format input (ContentDict)."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Dict format content
    contents = {"role": "user", "parts": [{"text": "Hello from dict format"}]}

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=contents, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == [
        {"text": "Hello from dict format", "type": "text"}
    ]


def test_generate_content_with_file_data(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with file_data (external file reference)."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Content with file_data
    file_data = genai_types.FileData(
        file_uri="gs://bucket/image.jpg", mime_type="image/jpeg"
    )
    content = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(text="What's in this image?"),
            genai_types.Part(file_data=file_data),
        ],
    )

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=content, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert len(messages[0]["content"]) == 2
    assert messages[0]["content"][0] == {
        "text": "What's in this image?",
        "type": "text",
    }
    assert messages[0]["content"][1]["type"] == "uri"
    assert messages[0]["content"][1]["modality"] == "image"
    assert messages[0]["content"][1]["mime_type"] == "image/jpeg"
    assert messages[0]["content"][1]["uri"] == "gs://bucket/image.jpg"


def test_generate_content_with_inline_data(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with inline_data (binary data)."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Content with inline binary data
    image_bytes = b"fake_image_binary_data"
    blob = genai_types.Blob(data=image_bytes, mime_type="image/png")
    content = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(text="Describe this image"),
            genai_types.Part(inline_data=blob),
        ],
    )

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=content, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert len(messages[0]["content"]) == 2
    assert messages[0]["content"][0] == {"text": "Describe this image", "type": "text"}
    assert messages[0]["content"][1]["type"] == "blob"
    assert messages[0]["content"][1]["mime_type"] == "image/png"
    # Binary data should be substituted for privacy
    assert messages[0]["content"][1]["content"] == BLOB_DATA_SUBSTITUTE


def test_generate_content_with_function_response(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with function_response (tool result)."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Conversation with function response (tool result)
    function_response = genai_types.FunctionResponse(
        id="call_123", name="get_weather", response={"output": "Sunny, 72F"}
    )
    contents = [
        genai_types.Content(
            role="user", parts=[genai_types.Part(text="What's the weather in Paris?")]
        ),
        genai_types.Content(
            role="user", parts=[genai_types.Part(function_response=function_response)]
        ),
    ]

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=contents, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    # First message is user message
    assert messages[0]["role"] == "tool"
    assert messages[0]["content"]["toolCallId"] == "call_123"
    assert messages[0]["content"]["toolName"] == "get_weather"
    assert messages[0]["content"]["output"] == '"Sunny, 72F"'


def test_generate_content_with_mixed_string_and_content(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with mixed string and Content objects in list."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Mix of strings and Content objects
    contents = [
        "Hello, this is a string message",
        genai_types.Content(
            role="model",
            parts=[genai_types.Part(text="Hi! How can I help you?")],
        ),
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(text="Tell me a joke")],
        ),
    ]

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=contents, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    # User message
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == [{"text": "Tell me a joke", "type": "text"}]


def test_generate_content_with_part_object_directly(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with Part object directly (not wrapped in Content)."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Part object directly
    part = genai_types.Part(text="Direct Part object")

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=part, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == [{"text": "Direct Part object", "type": "text"}]


def test_generate_content_with_list_of_dicts(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with list of dict format inputs."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # List of dicts (conversation in dict format)
    contents = [
        {"role": "user", "parts": [{"text": "First user message"}]},
        {"role": "model", "parts": [{"text": "First model response"}]},
        {"role": "user", "parts": [{"text": "Second user message"}]},
    ]

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=contents, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == [{"text": "Second user message", "type": "text"}]


def test_generate_content_with_dict_inline_data(
    sentry_init, capture_events, mock_genai_client
):
    """Test generate_content with dict format containing inline_data."""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    mock_http_response = create_mock_http_response(EXAMPLE_API_RESPONSE_JSON)

    # Dict with inline_data
    contents = {
        "role": "user",
        "parts": [
            {"text": "What's in this image?"},
            {"inline_data": {"data": b"fake_binary_data", "mime_type": "image/gif"}},
        ],
    }

    with mock.patch.object(
        mock_genai_client._api_client, "request", return_value=mock_http_response
    ):
        with start_transaction(name="google_genai"):
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents=contents, config=create_test_config()
            )

    (event,) = events
    invoke_span = event["spans"][0]

    messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert len(messages[0]["content"]) == 2
    assert messages[0]["content"][0] == {
        "text": "What's in this image?",
        "type": "text",
    }
    assert messages[0]["content"][1]["type"] == "blob"
    assert messages[0]["content"][1]["mime_type"] == "image/gif"
    assert messages[0]["content"][1]["content"] == BLOB_DATA_SUBSTITUTE


# Tests for extract_contents_messages function
def test_extract_contents_messages_none():
    """Test extract_contents_messages with None input"""
    result = extract_contents_messages(None)
    assert result == []


def test_extract_contents_messages_string():
    """Test extract_contents_messages with string input"""
    result = extract_contents_messages("Hello world")
    assert result == [{"role": "user", "content": "Hello world"}]


def test_extract_contents_messages_content_object():
    """Test extract_contents_messages with Content object"""
    content = genai_types.Content(
        role="user", parts=[genai_types.Part(text="Test message")]
    )
    result = extract_contents_messages(content)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [{"text": "Test message", "type": "text"}]


def test_extract_contents_messages_content_object_model_role():
    """Test extract_contents_messages with Content object having model role"""
    content = genai_types.Content(
        role="model", parts=[genai_types.Part(text="Assistant response")]
    )
    result = extract_contents_messages(content)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == [{"text": "Assistant response", "type": "text"}]


def test_extract_contents_messages_content_object_no_role():
    """Test extract_contents_messages with Content object without role"""
    content = genai_types.Content(parts=[genai_types.Part(text="No role message")])
    result = extract_contents_messages(content)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [{"text": "No role message", "type": "text"}]


def test_extract_contents_messages_part_object():
    """Test extract_contents_messages with Part object"""
    part = genai_types.Part(text="Direct part")
    result = extract_contents_messages(part)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [{"text": "Direct part", "type": "text"}]


def test_extract_contents_messages_file_data():
    """Test extract_contents_messages with file_data"""
    file_data = genai_types.FileData(
        file_uri="gs://bucket/file.jpg", mime_type="image/jpeg"
    )
    part = genai_types.Part(file_data=file_data)
    content = genai_types.Content(parts=[part])
    result = extract_contents_messages(content)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert len(result[0]["content"]) == 1
    blob_part = result[0]["content"][0]
    assert blob_part["type"] == "uri"
    assert blob_part["modality"] == "image"
    assert blob_part["mime_type"] == "image/jpeg"
    assert blob_part["uri"] == "gs://bucket/file.jpg"


def test_extract_contents_messages_inline_data():
    """Test extract_contents_messages with inline_data (binary)"""
    # Create inline data with bytes
    image_bytes = b"fake_image_data"
    blob = genai_types.Blob(data=image_bytes, mime_type="image/png")
    part = genai_types.Part(inline_data=blob)
    content = genai_types.Content(parts=[part])
    result = extract_contents_messages(content)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert len(result[0]["content"]) == 1
    blob_part = result[0]["content"][0]
    assert blob_part["type"] == "blob"
    assert blob_part["mime_type"] == "image/png"
    assert blob_part["content"] == BLOB_DATA_SUBSTITUTE


def test_extract_contents_messages_function_response():
    """Test extract_contents_messages with function_response (tool message)"""
    function_response = genai_types.FunctionResponse(
        id="call_123", name="get_weather", response={"output": "sunny"}
    )
    part = genai_types.Part(function_response=function_response)
    content = genai_types.Content(parts=[part])
    result = extract_contents_messages(content)

    assert len(result) == 1
    assert result[0]["role"] == "tool"
    assert result[0]["content"]["toolCallId"] == "call_123"
    assert result[0]["content"]["toolName"] == "get_weather"
    assert result[0]["content"]["output"] == '"sunny"'


def test_extract_contents_messages_function_response_with_output_key():
    """Test extract_contents_messages with function_response that has output key"""
    function_response = genai_types.FunctionResponse(
        id="call_456", name="get_time", response={"output": "3:00 PM", "error": None}
    )
    part = genai_types.Part(function_response=function_response)
    content = genai_types.Content(parts=[part])
    result = extract_contents_messages(content)

    assert len(result) == 1
    assert result[0]["role"] == "tool"
    assert result[0]["content"]["toolCallId"] == "call_456"
    assert result[0]["content"]["toolName"] == "get_time"
    # Should prefer "output" key
    assert result[0]["content"]["output"] == '"3:00 PM"'


def test_extract_contents_messages_mixed_parts():
    """Test extract_contents_messages with mixed content parts"""
    content = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(text="Text part"),
            genai_types.Part(
                file_data=genai_types.FileData(
                    file_uri="gs://bucket/image.jpg", mime_type="image/jpeg"
                )
            ),
        ],
    )
    result = extract_contents_messages(content)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert len(result[0]["content"]) == 2
    assert result[0]["content"][0] == {"text": "Text part", "type": "text"}
    assert result[0]["content"][1]["type"] == "uri"
    assert result[0]["content"][1]["modality"] == "image"
    assert result[0]["content"][1]["uri"] == "gs://bucket/image.jpg"


def test_extract_contents_messages_list():
    """Test extract_contents_messages with list input"""
    contents = [
        "First message",
        genai_types.Content(
            role="user", parts=[genai_types.Part(text="Second message")]
        ),
    ]
    result = extract_contents_messages(contents)

    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "First message"}
    assert result[1]["role"] == "user"
    assert result[1]["content"] == [{"text": "Second message", "type": "text"}]


def test_extract_contents_messages_dict_content():
    """Test extract_contents_messages with dict (ContentDict)"""
    content_dict = {"role": "user", "parts": [{"text": "Dict message"}]}
    result = extract_contents_messages(content_dict)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [{"text": "Dict message", "type": "text"}]


def test_extract_contents_messages_dict_with_text():
    """Test extract_contents_messages with dict containing text key"""
    content_dict = {"role": "user", "text": "Simple text"}
    result = extract_contents_messages(content_dict)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [{"text": "Simple text", "type": "text"}]


def test_extract_contents_messages_file_object():
    """Test extract_contents_messages with File object"""
    file_obj = genai_types.File(
        name="files/123", uri="gs://bucket/file.pdf", mime_type="application/pdf"
    )
    result = extract_contents_messages(file_obj)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert len(result[0]["content"]) == 1
    blob_part = result[0]["content"][0]
    assert blob_part["type"] == "uri"
    assert blob_part["modality"] == "document"
    assert blob_part["mime_type"] == "application/pdf"
    assert blob_part["uri"] == "gs://bucket/file.pdf"


@pytest.mark.skipif(
    not hasattr(genai_types, "PIL_Image") or genai_types.PIL_Image is None,
    reason="PIL not available",
)
def test_extract_contents_messages_pil_image():
    """Test extract_contents_messages with PIL.Image.Image"""
    try:
        from PIL import Image as PILImage

        # Create a simple test image
        img = PILImage.new("RGB", (10, 10), color="red")
        result = extract_contents_messages(img)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["content"]) == 1
        blob_part = result[0]["content"][0]
        assert blob_part["type"] == "blob"
        assert blob_part["mime_type"].startswith("image/")
        assert "content" in blob_part
        # Binary content is substituted with placeholder for privacy
        assert blob_part["content"] == "[Blob substitute]"
    except ImportError:
        pytest.skip("PIL not available")


def test_extract_contents_messages_tool_and_text():
    """Test extract_contents_messages with both tool message and text"""
    content = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(text="User question"),
            genai_types.Part(
                function_response=genai_types.FunctionResponse(
                    id="call_789", name="search", response={"output": "results"}
                )
            ),
        ],
    )
    result = extract_contents_messages(content)

    # Should have two messages: one user message and one tool message
    assert len(result) == 2
    # First should be user message with text
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [{"text": "User question", "type": "text"}]
    # Second should be tool message
    assert result[1]["role"] == "tool"
    assert result[1]["content"]["toolCallId"] == "call_789"
    assert result[1]["content"]["toolName"] == "search"


def test_extract_contents_messages_empty_parts():
    """Test extract_contents_messages with Content object with empty parts"""
    content = genai_types.Content(role="user", parts=[])
    result = extract_contents_messages(content)

    assert result == []


def test_extract_contents_messages_empty_list():
    """Test extract_contents_messages with empty list"""
    result = extract_contents_messages([])
    assert result == []


def test_extract_contents_messages_dict_inline_data():
    """Test extract_contents_messages with dict containing inline_data"""
    content_dict = {
        "role": "user",
        "parts": [{"inline_data": {"data": b"binary_data", "mime_type": "image/gif"}}],
    }
    result = extract_contents_messages(content_dict)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert len(result[0]["content"]) == 1
    blob_part = result[0]["content"][0]
    assert blob_part["type"] == "blob"
    assert blob_part["mime_type"] == "image/gif"
    assert blob_part["content"] == BLOB_DATA_SUBSTITUTE


def test_extract_contents_messages_dict_function_response():
    """Test extract_contents_messages with dict containing function_response"""
    content_dict = {
        "role": "user",
        "parts": [
            {
                "function_response": {
                    "id": "dict_call_1",
                    "name": "dict_tool",
                    "response": {"result": "success"},
                }
            }
        ],
    }
    result = extract_contents_messages(content_dict)

    assert len(result) == 1
    assert result[0]["role"] == "tool"
    assert result[0]["content"]["toolCallId"] == "dict_call_1"
    assert result[0]["content"]["toolName"] == "dict_tool"
    assert result[0]["content"]["output"] == '{"result": "success"}'


def test_extract_contents_messages_object_with_text_attribute():
    """Test extract_contents_messages with object that has text attribute"""

    class TextObject:
        def __init__(self):
            self.text = "Object text"

    obj = TextObject()
    result = extract_contents_messages(obj)

    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == [{"text": "Object text", "type": "text"}]

import json
import pytest
from unittest import mock

from google import genai
from google.genai import types as genai_types

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration


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
        # Convert string to Content for system instruction
        if isinstance(system_instruction, str):
            system_instruction = genai_types.Content(
                parts=[genai_types.Part(text=system_instruction)], role="system"
            )
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

    # Should have 2 spans: invoke_agent and chat
    assert len(event["spans"]) == 2
    invoke_span, chat_span = event["spans"]

    # Check invoke_agent span
    assert invoke_span["op"] == OP.GEN_AI_INVOKE_AGENT
    assert invoke_span["description"] == "invoke_agent"
    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "gemini-1.5-flash"
    assert invoke_span["data"][SPANDATA.GEN_AI_SYSTEM] == "gcp.gemini"
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gemini-1.5-flash"
    assert invoke_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "invoke_agent"

    # Check chat span
    assert chat_span["op"] == OP.GEN_AI_CHAT
    assert chat_span["description"] == "chat gemini-1.5-flash"
    assert chat_span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "chat"
    assert chat_span["data"][SPANDATA.GEN_AI_SYSTEM] == "gcp.gemini"
    assert chat_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gemini-1.5-flash"

    if send_default_pii and include_prompts:
        # Messages are serialized as JSON strings
        messages = json.loads(invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES])
        assert messages == [{"role": "user", "content": "Tell me a joke"}]

        # Response text is stored as a JSON array
        response_text = chat_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
        # Parse the JSON array
        response_texts = json.loads(response_text)
        assert response_texts == ["Hello! How can I help you today?"]
    else:
        assert SPANDATA.GEN_AI_REQUEST_MESSAGES not in invoke_span["data"]
        assert SPANDATA.GEN_AI_RESPONSE_TEXT not in chat_span["data"]

    # Check token usage
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 10
    # Output tokens now include reasoning tokens: candidates_token_count (20) + thoughts_token_count (3) = 23
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 23
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 30
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 5
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING] == 3

    # Check configuration parameters
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_TEMPERATURE] == 0.7
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] == 100


def test_generate_content_with_system_instruction(
    sentry_init, capture_events, mock_genai_client
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
            config = create_test_config(
                system_instruction="You are a helpful assistant",
                temperature=0.5,
            )
            mock_genai_client.models.generate_content(
                model="gemini-1.5-flash", contents="What is 2+2?", config=config
            )

    (event,) = events
    invoke_span = event["spans"][0]

    # Check that system instruction is included in messages
    # (PII is enabled and include_prompts is True in this test)
    messages_str = invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    # Parse the JSON string to verify content
    messages = json.loads(messages_str)
    assert len(messages) == 2
    assert messages[0] == {"role": "system", "content": "You are a helpful assistant"}
    assert messages[1] == {"role": "user", "content": "What is 2+2?"}


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
            "totalTokenCount": 12,  # Not set in intermediate chunks
        },
        "responseId": "response-id-stream-123",
        "modelVersion": "gemini-1.5-flash",
    }

    # Chunk 2: Second part of text with more usage metadata
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

    # There should be 2 spans: invoke_agent and chat
    assert len(event["spans"]) == 2
    invoke_span = event["spans"][0]
    chat_span = event["spans"][1]

    # Check that streaming flag is set on both spans
    assert invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True
    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True

    # Verify accumulated response text (all chunks combined)
    expected_full_text = "Hello! How can I help you today?"
    # Response text is stored as a JSON string
    chat_response_text = json.loads(chat_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT])
    invoke_response_text = json.loads(
        invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_TEXT]
    )
    assert chat_response_text == [expected_full_text]
    assert invoke_response_text == [expected_full_text]

    # Verify finish reasons (only the final chunk has a finish reason)
    # When there's a single finish reason, it's stored as a plain string (not JSON)
    assert SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS in chat_span["data"]
    assert SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS in invoke_span["data"]
    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == "STOP"
    assert invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] == "STOP"

    # Verify token counts - should reflect accumulated values
    # Input tokens: max of all chunks = 10
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 30
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 30

    # Output tokens: candidates (2 + 3 + 7 = 12) + reasoning (3) = 15
    # Note: output_tokens includes both candidates and reasoning tokens
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 15
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 15

    # Total tokens: from the last chunk
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 50
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] == 50

    # Cached tokens: max of all chunks = 5
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 5
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] == 5

    # Reasoning tokens: sum of thoughts_token_count = 3
    assert chat_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING] == 3
    assert invoke_span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING] == 3

    # Verify model name
    assert chat_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gemini-1.5-flash"
    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "gemini-1.5-flash"


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
    chat_span = event["spans"][1]

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
    chat_span = event["spans"][1]

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
    assert len(event["spans"]) == 2


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
    chat_span = event["spans"][1]

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
    chat_span = event["spans"][1]  # The chat span

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
                contents=small_content,
                config=create_test_config(
                    system_instruction=large_content,
                ),
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

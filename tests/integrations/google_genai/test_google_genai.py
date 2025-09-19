import json
import pytest
from unittest import mock

try:
    from google import genai
    from google.genai import types as genai_types
    from google.genai.models import Models
except ImportError:
    # If google.genai is not installed, skip the tests
    pytest.skip("google-genai not installed", allow_module_level=True)

from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration


# Create test responses using real types
def create_test_part(text):
    """Create a Part with text content."""
    return genai_types.Part(text=text)


def create_test_content(parts):
    """Create Content with the given parts."""
    return genai_types.Content(parts=parts, role="model")


def create_test_candidate(content, finish_reason=None):
    """Create a Candidate with content."""
    return genai_types.Candidate(
        content=content,
        finish_reason=finish_reason or genai_types.FinishReason.STOP,
    )


def create_test_usage_metadata(
    prompt_token_count=0,
    candidates_token_count=0,
    total_token_count=0,
    cached_content_token_count=0,
    thoughts_token_count=0,
):
    """Create usage metadata."""
    return genai_types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt_token_count,
        candidates_token_count=candidates_token_count,
        total_token_count=total_token_count,
        cached_content_token_count=cached_content_token_count,
        thoughts_token_count=thoughts_token_count,
    )


def create_test_response(
    candidates,
    usage_metadata=None,
    response_id=None,
    model_version=None,
):
    """Create a GenerateContentResponse."""
    return genai_types.GenerateContentResponse(
        candidates=candidates,
        usage_metadata=usage_metadata,
        response_id=response_id,
        model_version=model_version,
    )


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


# Sample responses
EXAMPLE_RESPONSE = create_test_response(
    candidates=[
        create_test_candidate(
            content=create_test_content(
                parts=[create_test_part("Hello! How can I help you today?")]
            ),
            finish_reason=genai_types.FinishReason.STOP,
        )
    ],
    usage_metadata=create_test_usage_metadata(
        prompt_token_count=10,
        candidates_token_count=20,
        total_token_count=30,
        cached_content_token_count=5,
        thoughts_token_count=3,
    ),
    response_id="response-id-123",
    model_version="gemini-1.5-flash",
)


@pytest.fixture
def mock_models_instance():
    """Mock the Models instance and its generate_content method"""
    # Create a mock API client
    mock_api_client = mock.Mock()

    # Create a real Models instance with the mock API client
    models_instance = Models(mock_api_client)

    # Return the instance for use in tests
    yield models_instance


def setup_mock_generate_content(mock_instance, return_value=None, side_effect=None):
    """Helper to set up the mock generate_content method with proper wrapping."""
    # Create a mock method that simulates the behavior
    original_mock = mock.Mock()
    if side_effect:
        original_mock.side_effect = side_effect
    else:
        original_mock.return_value = return_value

    # Create a bound method that will receive self as first argument
    def mock_generate_content(self, *args, **kwargs):
        # Call the original mock with all arguments
        return original_mock(*args, **kwargs)

    # Apply the integration patch to our mock method
    from sentry_sdk.integrations.google_genai import _wrap_generate_content

    wrapped_method = _wrap_generate_content(mock_generate_content)

    # Bind the wrapped method to the mock instance
    mock_instance.generate_content = wrapped_method.__get__(
        mock_instance, type(mock_instance)
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
def test_nonstreaming_generate_content(
    sentry_init, capture_events, send_default_pii, include_prompts, mock_models_instance
):
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="google_genai"):
        config = create_test_config(temperature=0.7, max_output_tokens=100)
        # Create an instance and call generate_content
        # Use the mock instance from the fixture
        response = mock_models_instance.generate_content(
            "gemini-1.5-flash", "Tell me a joke", config=config
        )

    assert response == EXAMPLE_RESPONSE

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
    sentry_init, capture_events, mock_models_instance
):
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="google_genai"):
        config = create_test_config(
            system_instruction="You are a helpful assistant",
            temperature=0.5,
        )
        # Verify config has system_instruction
        assert hasattr(config, "system_instruction")
        assert config.system_instruction is not None

        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "What is 2+2?", config=config
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


def test_generate_content_with_tools(sentry_init, capture_events, mock_models_instance):
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

    # Mock the response to include tool usage
    tool_response = create_test_response(
        candidates=[
            create_test_candidate(
                content=create_test_content(
                    parts=[create_test_part("I'll check the weather.")]
                ),
                finish_reason=genai_types.FinishReason.STOP,
            )
        ],
        usage_metadata=create_test_usage_metadata(
            prompt_token_count=15, candidates_token_count=10, total_token_count=25
        ),
    )

    setup_mock_generate_content(mock_models_instance, return_value=tool_response)

    with start_transaction(name="google_genai"):
        config = create_test_config(tools=[get_weather, mock_tool])
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "What's the weather?", config=config
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


def test_error_handling(sentry_init, capture_events, mock_models_instance):
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Mock an error
    setup_mock_generate_content(
        mock_models_instance, side_effect=Exception("API Error")
    )

    with start_transaction(name="google_genai"):
        with pytest.raises(Exception, match="API Error"):
            # Use the mock instance from the fixture
            mock_models_instance.generate_content(
                "gemini-1.5-flash", "This will fail", config=create_test_config()
            )

    # Should have both transaction and error events
    assert len(events) == 2
    error_event, transaction_event = events

    assert error_event["level"] == "error"
    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert error_event["exception"]["values"][0]["value"] == "API Error"
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "google_genai"


def test_streaming_generate_content(sentry_init, capture_events, mock_models_instance):
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="google_genai"):
        config = create_test_config()
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "Stream me a response", config=config, stream=True
        )

    (event,) = events
    invoke_span = event["spans"][0]

    # Check that streaming flag is set
    assert invoke_span["data"][SPANDATA.GEN_AI_RESPONSE_STREAMING] is True


def test_different_content_formats(sentry_init, capture_events, mock_models_instance):
    """Test different content formats that can be passed to generate_content"""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    # Test with list of content parts
    with start_transaction(name="test1"):
        config = create_test_config()
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash",
            [{"text": "Part 1"}, {"text": "Part 2"}],
            config=config,
        )

    # Test with object that has text attribute
    class ContentWithText:
        def __init__(self, text):
            self.text = text

    with start_transaction(name="test2"):
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash",
            ContentWithText("Object with text"),
            config=config,
        )

    events_list = list(events)
    assert len(events_list) == 2

    # Check first transaction (PII is enabled and include_prompts is True)
    messages1_str = events_list[0]["spans"][0]["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    # Parse the JSON string to verify content
    import json

    messages1 = json.loads(messages1_str)
    assert messages1[0]["content"] == "Part 1 Part 2"

    # Check second transaction
    messages2_str = events_list[1]["spans"][0]["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
    messages2 = json.loads(messages2_str)
    assert messages2[0]["content"] == "Object with text"


def test_span_origin(sentry_init, capture_events, mock_models_instance):
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="google_genai"):
        config = create_test_config()
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "Test origin", config=config
        )

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    for span in event["spans"]:
        assert span["origin"] == "auto.ai.google_genai"


def test_response_without_usage_metadata(
    sentry_init, capture_events, mock_models_instance
):
    """Test handling of responses without usage metadata"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Create response without usage metadata
    response_without_usage = create_test_response(
        candidates=[
            create_test_candidate(
                content=create_test_content(parts=[create_test_part("No usage data")]),
                finish_reason=genai_types.FinishReason.STOP,
            )
        ],
        usage_metadata=None,
    )

    setup_mock_generate_content(
        mock_models_instance, return_value=response_without_usage
    )

    with start_transaction(name="google_genai"):
        config = create_test_config()
        # Use the mock instance from the fixture
        mock_models_instance.generate_content("gemini-1.5-flash", "Test", config=config)

    (event,) = events
    chat_span = event["spans"][1]

    # Usage data should not be present
    assert SPANDATA.GEN_AI_USAGE_INPUT_TOKENS not in chat_span["data"]
    assert SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS not in chat_span["data"]
    assert SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS not in chat_span["data"]


def test_multiple_candidates(sentry_init, capture_events, mock_models_instance):
    """Test handling of multiple response candidates"""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    # Create response with multiple candidates
    multi_candidate_response = create_test_response(
        candidates=[
            create_test_candidate(
                content=create_test_content(parts=[create_test_part("Response 1")]),
                finish_reason=genai_types.FinishReason.STOP,
            ),
            create_test_candidate(
                content=create_test_content(parts=[create_test_part("Response 2")]),
                finish_reason=genai_types.FinishReason.MAX_TOKENS,
            ),
        ],
        usage_metadata=create_test_usage_metadata(
            prompt_token_count=5, candidates_token_count=15, total_token_count=20
        ),
    )

    setup_mock_generate_content(
        mock_models_instance, return_value=multi_candidate_response
    )

    with start_transaction(name="google_genai"):
        config = create_test_config()
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "Generate multiple", config=config
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


def test_model_as_string(sentry_init, capture_events, mock_models_instance):
    """Test when model is passed as a string"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="google_genai"):
        # Pass model as string directly
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-pro", "Test prompt", config=create_test_config()
        )

    (event,) = events
    invoke_span = event["spans"][0]

    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "gemini-1.5-pro"
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gemini-1.5-pro"


def test_predefined_tools(sentry_init, capture_events, mock_models_instance):
    """Test handling of predefined Google tools"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Create mock tools with different predefined tool types
    # Create non-callable objects to represent predefined tools
    class MockGoogleSearchTool:
        def __init__(self):
            self.google_search_retrieval = mock.Mock()
            self.function_declarations = None

    class MockCodeExecutionTool:
        def __init__(self):
            self.code_execution = mock.Mock()
            self.function_declarations = None

    class MockRetrievalTool:
        def __init__(self):
            self.retrieval = mock.Mock()
            self.function_declarations = None

    google_search_tool = MockGoogleSearchTool()
    code_execution_tool = MockCodeExecutionTool()
    retrieval_tool = MockRetrievalTool()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    # Create a mock config instead of using create_test_config which validates
    mock_config = mock.Mock()
    mock_config.tools = [google_search_tool, code_execution_tool, retrieval_tool]
    mock_config.temperature = None
    mock_config.top_p = None
    mock_config.top_k = None
    mock_config.max_output_tokens = None
    mock_config.presence_penalty = None
    mock_config.frequency_penalty = None
    mock_config.seed = None
    mock_config.system_instruction = None

    with start_transaction(name="google_genai"):
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "Use tools", config=mock_config
        )

    (event,) = events
    invoke_span = event["spans"][0]

    # Check that tools are recorded (data is serialized as a string)
    tools_data_str = invoke_span["data"][SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS]
    tools_data = json.loads(tools_data_str)
    assert len(tools_data) == 3
    assert tools_data[0]["name"] == "google_search_retrieval"
    assert tools_data[1]["name"] == "code_execution"
    assert tools_data[2]["name"] == "retrieval"


def test_all_configuration_parameters(
    sentry_init, capture_events, mock_models_instance
):
    """Test that all configuration parameters are properly recorded"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

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
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "Test all params", config=config
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


def test_model_with_name_attribute(sentry_init, capture_events, mock_models_instance):
    """Test when model is an object with name attribute"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Create a model object with name attribute
    class ModelWithName:
        name = "gemini-ultra"

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="google_genai"):
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            ModelWithName(), "Test prompt", config=create_test_config()
        )

    (event,) = events
    invoke_span = event["spans"][0]

    assert invoke_span["data"][SPANDATA.GEN_AI_AGENT_NAME] == "gemini-ultra"
    assert invoke_span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "gemini-ultra"


def test_empty_response(sentry_init, capture_events, mock_models_instance):
    """Test handling of empty or None response"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Return None response
    setup_mock_generate_content(mock_models_instance, return_value=None)

    with start_transaction(name="google_genai"):
        # Use the mock instance from the fixture
        response = mock_models_instance.generate_content(
            "gemini-1.5-flash", "Test", config=create_test_config()
        )

    assert response is None

    (event,) = events
    # Should still create spans even with None response
    assert len(event["spans"]) == 2


def test_response_with_different_id_fields(
    sentry_init, capture_events, mock_models_instance
):
    """Test handling of different response ID field names"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Test with response_id instead of id
    response_with_response_id = create_test_response(
        candidates=[
            create_test_candidate(
                content=create_test_content(parts=[create_test_part("Test")]),
                finish_reason=genai_types.FinishReason.STOP,
            )
        ],
    )
    response_with_response_id.response_id = "resp-456"
    response_with_response_id.model_version = "gemini-1.5-flash-001"

    setup_mock_generate_content(
        mock_models_instance, return_value=response_with_response_id
    )

    with start_transaction(name="google_genai"):
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "Test", config=create_test_config()
        )

    (event,) = events
    chat_span = event["spans"][1]

    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_ID] == "resp-456"
    assert chat_span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "gemini-1.5-flash-001"


def test_integration_not_enabled(sentry_init, mock_models_instance):
    """Test that integration doesn't interfere when not enabled"""
    sentry_init(
        integrations=[],  # No GoogleGenAIIntegration
        traces_sample_rate=1.0,
    )

    # Mock the method without wrapping (since integration is not enabled)
    mock_models_instance.generate_content = mock.Mock(return_value=EXAMPLE_RESPONSE)

    # Should work without creating spans
    # Use the mock instance from the fixture
    response = mock_models_instance.generate_content(
        "gemini-1.5-flash", "Test", config=create_test_config()
    )

    assert response == EXAMPLE_RESPONSE


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


def test_contents_as_none(sentry_init, capture_events, mock_models_instance):
    """Test handling when contents parameter is None"""
    sentry_init(
        integrations=[GoogleGenAIIntegration(include_prompts=True)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    setup_mock_generate_content(mock_models_instance, return_value=EXAMPLE_RESPONSE)

    with start_transaction(name="google_genai"):
        # Use the mock instance from the fixture
        mock_models_instance.generate_content(
            "gemini-1.5-flash", None, config=create_test_config()
        )

    (event,) = events
    invoke_span = event["spans"][0]

    # Should handle None contents gracefully
    messages = invoke_span["data"].get(SPANDATA.GEN_AI_REQUEST_MESSAGES, [])
    # Should only have system message if any, not user message
    assert all(msg["role"] != "user" or msg["content"] is not None for msg in messages)


def test_tool_calls_extraction(sentry_init, capture_events, mock_models_instance):
    """Test extraction of tool/function calls from response"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Create a response with function calls
    function_call_response = create_test_response(
        candidates=[
            create_test_candidate(
                content=genai_types.Content(
                    parts=[
                        genai_types.Part(text="I'll help you with that."),
                        genai_types.Part(
                            function_call=genai_types.FunctionCall(
                                name="get_weather",
                                args={"location": "San Francisco", "unit": "celsius"},
                            )
                        ),
                        genai_types.Part(
                            function_call=genai_types.FunctionCall(
                                name="get_time", args={"timezone": "PST"}
                            )
                        ),
                    ],
                    role="model",
                ),
                finish_reason=genai_types.FinishReason.STOP,
            )
        ],
        usage_metadata=create_test_usage_metadata(
            prompt_token_count=20, candidates_token_count=30, total_token_count=50
        ),
    )

    setup_mock_generate_content(
        mock_models_instance, return_value=function_call_response
    )

    with start_transaction(name="google_genai"):
        mock_models_instance.generate_content(
            "gemini-1.5-flash",
            "What's the weather and time?",
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


def test_tool_calls_with_automatic_function_calling(
    sentry_init, capture_events, mock_models_instance
):
    """Test extraction of tool calls from automatic_function_calling_history"""
    sentry_init(
        integrations=[GoogleGenAIIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Create a response with automatic function calling history
    response_with_auto_calls = create_test_response(
        candidates=[
            create_test_candidate(
                content=create_test_content(
                    parts=[create_test_part("Here's the information you requested.")]
                ),
                finish_reason=genai_types.FinishReason.STOP,
            )
        ],
    )

    # Add automatic_function_calling_history
    response_with_auto_calls.automatic_function_calling_history = [
        genai_types.Content(
            parts=[
                genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        name="search_database",
                        args={"query": "user stats", "limit": 10},
                    )
                )
            ],
            role="model",
        ),
        genai_types.Content(
            parts=[
                genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        name="search_database", response={"results": ["item1", "item2"]}
                    )
                )
            ],
            role="function",
        ),
    ]

    setup_mock_generate_content(
        mock_models_instance, return_value=response_with_auto_calls
    )

    with start_transaction(name="google_genai"):
        mock_models_instance.generate_content(
            "gemini-1.5-flash", "Get user statistics", config=create_test_config()
        )

    (event,) = events
    chat_span = event["spans"][1]  # The chat span

    # Check that tool calls from automatic history are extracted
    assert SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS in chat_span["data"]

    tool_calls = json.loads(chat_span["data"][SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS])

    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "search_database"
    assert tool_calls[0]["type"] == "function_call"
    # Arguments are serialized as JSON strings
    assert json.loads(tool_calls[0]["arguments"]) == {
        "query": "user stats",
        "limit": 10,
    }

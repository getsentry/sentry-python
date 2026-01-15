import json
import uuid

import pytest

import sentry_sdk
from sentry_sdk._types import (
    AnnotatedValue,
    SENSITIVE_DATA_SUBSTITUTE,
    BLOB_DATA_SUBSTITUTE,
)
from sentry_sdk.ai.monitoring import ai_track
from sentry_sdk.ai.utils import (
    MAX_GEN_AI_MESSAGE_BYTES,
    MAX_SINGLE_MESSAGE_CONTENT_CHARS,
    set_data_normalized,
    truncate_and_annotate_messages,
    truncate_messages_by_size,
    _find_truncation_index,
    parse_data_uri,
    redact_blob_message_parts,
    get_modality_from_mime_type,
    transform_openai_content_part,
    transform_anthropic_content_part,
    transform_google_content_part,
    transform_generic_content_part,
    transform_content_part,
    transform_message_content,
)
from sentry_sdk.serializer import serialize
from sentry_sdk.utils import safe_serialize


def test_ai_track(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @ai_track("my tool")
    def tool(**kwargs):
        pass

    @ai_track("some test pipeline")
    def pipeline():
        tool()

    with sentry_sdk.start_transaction():
        pipeline()

    transaction = events[0]
    assert transaction["type"] == "transaction"
    assert len(transaction["spans"]) == 2
    spans = transaction["spans"]

    ai_pipeline_span = spans[0] if spans[0]["op"] == "ai.pipeline" else spans[1]
    ai_run_span = spans[0] if spans[0]["op"] == "ai.run" else spans[1]

    assert ai_pipeline_span["description"] == "some test pipeline"
    assert ai_run_span["description"] == "my tool"


def test_ai_track_with_tags(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @ai_track("my tool")
    def tool(**kwargs):
        pass

    @ai_track("some test pipeline")
    def pipeline():
        tool()

    with sentry_sdk.start_transaction():
        pipeline(sentry_tags={"user": "colin"}, sentry_data={"some_data": "value"})

    transaction = events[0]
    assert transaction["type"] == "transaction"
    assert len(transaction["spans"]) == 2
    spans = transaction["spans"]

    ai_pipeline_span = spans[0] if spans[0]["op"] == "ai.pipeline" else spans[1]
    ai_run_span = spans[0] if spans[0]["op"] == "ai.run" else spans[1]

    assert ai_pipeline_span["description"] == "some test pipeline"
    print(ai_pipeline_span)
    assert ai_pipeline_span["tags"]["user"] == "colin"
    assert ai_pipeline_span["data"]["some_data"] == "value"
    assert ai_run_span["description"] == "my tool"


@pytest.mark.asyncio
async def test_ai_track_async(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @ai_track("my async tool")
    async def async_tool(**kwargs):
        pass

    @ai_track("some async test pipeline")
    async def async_pipeline():
        await async_tool()

    with sentry_sdk.start_transaction():
        await async_pipeline()

    transaction = events[0]
    assert transaction["type"] == "transaction"
    assert len(transaction["spans"]) == 2
    spans = transaction["spans"]

    ai_pipeline_span = spans[0] if spans[0]["op"] == "ai.pipeline" else spans[1]
    ai_run_span = spans[0] if spans[0]["op"] == "ai.run" else spans[1]

    assert ai_pipeline_span["description"] == "some async test pipeline"
    assert ai_run_span["description"] == "my async tool"


@pytest.mark.asyncio
async def test_ai_track_async_with_tags(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @ai_track("my async tool")
    async def async_tool(**kwargs):
        pass

    @ai_track("some async test pipeline")
    async def async_pipeline():
        await async_tool()

    with sentry_sdk.start_transaction():
        await async_pipeline(
            sentry_tags={"user": "czyber"}, sentry_data={"some_data": "value"}
        )

    transaction = events[0]
    assert transaction["type"] == "transaction"
    assert len(transaction["spans"]) == 2
    spans = transaction["spans"]

    ai_pipeline_span = spans[0] if spans[0]["op"] == "ai.pipeline" else spans[1]
    ai_run_span = spans[0] if spans[0]["op"] == "ai.run" else spans[1]

    assert ai_pipeline_span["description"] == "some async test pipeline"
    assert ai_pipeline_span["tags"]["user"] == "czyber"
    assert ai_pipeline_span["data"]["some_data"] == "value"
    assert ai_run_span["description"] == "my async tool"


def test_ai_track_with_explicit_op(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @ai_track("my tool", op="custom.operation")
    def tool(**kwargs):
        pass

    with sentry_sdk.start_transaction():
        tool()

    transaction = events[0]
    assert transaction["type"] == "transaction"
    assert len(transaction["spans"]) == 1
    span = transaction["spans"][0]

    assert span["description"] == "my tool"
    assert span["op"] == "custom.operation"


@pytest.mark.asyncio
async def test_ai_track_async_with_explicit_op(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    @ai_track("my async tool", op="custom.async.operation")
    async def async_tool(**kwargs):
        pass

    with sentry_sdk.start_transaction():
        await async_tool()

    transaction = events[0]
    assert transaction["type"] == "transaction"
    assert len(transaction["spans"]) == 1
    span = transaction["spans"][0]

    assert span["description"] == "my async tool"
    assert span["op"] == "custom.async.operation"


@pytest.fixture
def sample_messages():
    """Sample messages similar to what gen_ai integrations would use"""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {
            "role": "user",
            "content": "What is the difference between a list and a tuple in Python?",
        },
        {
            "role": "assistant",
            "content": "Lists are mutable and use [], tuples are immutable and use ().",
        },
        {"role": "user", "content": "Can you give me some examples?"},
        {
            "role": "assistant",
            "content": "Sure! Here are examples:\n\n```python\n# List\nmy_list = [1, 2, 3]\nmy_list.append(4)\n\n# Tuple\nmy_tuple = (1, 2, 3)\n# my_tuple.append(4) would error\n```",
        },
    ]


@pytest.fixture
def large_messages():
    """Messages that will definitely exceed size limits"""
    large_content = "This is a very long message. " * 100
    return [
        {"role": "system", "content": large_content},
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": large_content},
        {"role": "user", "content": large_content},
    ]


class TestTruncateMessagesBySize:
    def test_no_truncation_needed(self, sample_messages):
        """Test that messages under the limit are not truncated"""
        result, truncation_index = truncate_messages_by_size(
            sample_messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES
        )
        assert len(result) == len(sample_messages)
        assert result == sample_messages
        assert truncation_index == 0

    def test_truncation_removes_oldest_first(self, large_messages):
        """Test that oldest messages are removed first during truncation"""
        small_limit = 3000
        result, truncation_index = truncate_messages_by_size(
            large_messages, max_bytes=small_limit
        )
        assert len(result) < len(large_messages)

        assert result[-1] == large_messages[-1]
        assert truncation_index == len(large_messages) - len(result)

    def test_empty_messages_list(self):
        """Test handling of empty messages list"""
        result, truncation_index = truncate_messages_by_size(
            [], max_bytes=MAX_GEN_AI_MESSAGE_BYTES // 500
        )
        assert result == []
        assert truncation_index == 0

    def test_find_truncation_index(
        self,
    ):
        """Test that the truncation index is found correctly"""
        # when represented in JSON, these are each 7 bytes long
        messages = ["A" * 5, "B" * 5, "C" * 5, "D" * 5, "E" * 5]
        truncation_index = _find_truncation_index(messages, 20)
        assert truncation_index == 3
        assert messages[truncation_index:] == ["D" * 5, "E" * 5]

        messages = ["A" * 5, "B" * 5, "C" * 5, "D" * 5, "E" * 5]
        truncation_index = _find_truncation_index(messages, 40)
        assert truncation_index == 0
        assert messages[truncation_index:] == [
            "A" * 5,
            "B" * 5,
            "C" * 5,
            "D" * 5,
            "E" * 5,
        ]

    def test_progressive_truncation(self, large_messages):
        """Test that truncation works progressively with different limits"""
        limits = [
            MAX_GEN_AI_MESSAGE_BYTES // 5,
            MAX_GEN_AI_MESSAGE_BYTES // 10,
            MAX_GEN_AI_MESSAGE_BYTES // 25,
            MAX_GEN_AI_MESSAGE_BYTES // 100,
            MAX_GEN_AI_MESSAGE_BYTES // 500,
        ]
        prev_count = len(large_messages)

        for limit in limits:
            result = truncate_messages_by_size(large_messages, max_bytes=limit)
            current_count = len(result)

            assert current_count <= prev_count
            assert current_count >= 1
            prev_count = current_count

    def test_single_message_truncation(self):
        large_content = "This is a very long message. " * 10_000

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": large_content},
        ]

        result, truncation_index = truncate_messages_by_size(
            messages, max_single_message_chars=MAX_SINGLE_MESSAGE_CONTENT_CHARS
        )

        assert len(result) == 1
        assert (
            len(result[0]["content"].rstrip("...")) <= MAX_SINGLE_MESSAGE_CONTENT_CHARS
        )

        # If the last message is too large, the system message is not present
        system_msgs = [m for m in result if m.get("role") == "system"]
        assert len(system_msgs) == 0

        # Confirm the user message is truncated with '...'
        user_msgs = [m for m in result if m.get("role") == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"].endswith("...")
        assert len(user_msgs[0]["content"]) < len(large_content)


class TestTruncateAndAnnotateMessages:
    def test_no_truncation_returns_list(self, sample_messages):
        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_id"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        span = MockSpan()
        scope = MockScope()
        result = truncate_and_annotate_messages(sample_messages, span, scope)

        assert isinstance(result, list)
        assert not isinstance(result, AnnotatedValue)
        assert len(result) == len(sample_messages)
        assert result == sample_messages
        assert span.span_id not in scope._gen_ai_original_message_count

    def test_truncation_sets_metadata_on_scope(self, large_messages):
        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_id"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        small_limit = 3000
        span = MockSpan()
        scope = MockScope()
        original_count = len(large_messages)
        result = truncate_and_annotate_messages(
            large_messages, span, scope, max_bytes=small_limit
        )

        assert isinstance(result, list)
        assert not isinstance(result, AnnotatedValue)
        assert len(result) < len(large_messages)
        assert scope._gen_ai_original_message_count[span.span_id] == original_count

    def test_scope_tracks_original_message_count(self, large_messages):
        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_id"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        small_limit = 3000
        original_count = len(large_messages)
        span = MockSpan()
        scope = MockScope()

        result = truncate_and_annotate_messages(
            large_messages, span, scope, max_bytes=small_limit
        )

        assert scope._gen_ai_original_message_count[span.span_id] == original_count
        assert len(result) == 1

    def test_empty_messages_returns_none(self):
        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_id"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        span = MockSpan()
        scope = MockScope()
        result = truncate_and_annotate_messages([], span, scope)
        assert result is None

        result = truncate_and_annotate_messages(None, span, scope)
        assert result is None

    def test_truncated_messages_newest_first(self, large_messages):
        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_id"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        small_limit = 3000
        span = MockSpan()
        scope = MockScope()
        result = truncate_and_annotate_messages(
            large_messages, span, scope, max_bytes=small_limit
        )

        assert isinstance(result, list)
        assert result[0] == large_messages[-len(result)]

    def test_preserves_original_messages_with_blobs(self):
        """Test that truncate_and_annotate_messages doesn't mutate the original messages"""

        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_id"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "What's in this image?", "type": "text"},
                    {
                        "type": "blob",
                        "modality": "image",
                        "content": "data:image/jpeg;base64,original_content",
                    },
                ],
            }
        ]

        original_blob_content = messages[0]["content"][1]["content"]

        span = MockSpan()
        scope = MockScope()

        # This should NOT mutate the original messages
        result = truncate_and_annotate_messages(messages, span, scope)

        # Verify original is unchanged
        assert messages[0]["content"][1]["content"] == original_blob_content

        # Verify result has redacted content
        assert result[0]["content"][1]["content"] == BLOB_DATA_SUBSTITUTE


class TestClientAnnotation:
    def test_client_wraps_truncated_messages_in_annotated_value(self, large_messages):
        """Test that client.py properly wraps truncated messages in AnnotatedValue using scope data"""
        from sentry_sdk._types import AnnotatedValue
        from sentry_sdk.consts import SPANDATA

        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_123"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        small_limit = 3000
        span = MockSpan()
        scope = MockScope()
        original_count = len(large_messages)

        # Simulate what integrations do
        truncated_messages = truncate_and_annotate_messages(
            large_messages, span, scope, max_bytes=small_limit
        )
        span.set_data(SPANDATA.GEN_AI_REQUEST_MESSAGES, truncated_messages)

        # Verify metadata was set on scope
        assert span.span_id in scope._gen_ai_original_message_count
        assert scope._gen_ai_original_message_count[span.span_id] > 0

        # Simulate what client.py does
        event = {"spans": [{"span_id": span.span_id, "data": span.data.copy()}]}

        # Mimic client.py logic - using scope to get the original length
        for event_span in event["spans"]:
            span_id = event_span.get("span_id")
            span_data = event_span.get("data", {})
            if (
                span_id
                and span_id in scope._gen_ai_original_message_count
                and SPANDATA.GEN_AI_REQUEST_MESSAGES in span_data
            ):
                messages = span_data[SPANDATA.GEN_AI_REQUEST_MESSAGES]
                n_original_count = scope._gen_ai_original_message_count[span_id]

                span_data[SPANDATA.GEN_AI_REQUEST_MESSAGES] = AnnotatedValue(
                    safe_serialize(messages),
                    {"len": n_original_count},
                )

        # Verify the annotation happened
        messages_value = event["spans"][0]["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert isinstance(messages_value, AnnotatedValue)
        assert messages_value.metadata["len"] == original_count
        assert isinstance(messages_value.value, str)

    def test_annotated_value_shows_correct_original_length(self, large_messages):
        """Test that the annotated value correctly shows the original message count before truncation"""
        from sentry_sdk.consts import SPANDATA

        class MockSpan:
            def __init__(self):
                self.span_id = "test_span_456"
                self.data = {}

            def set_data(self, key, value):
                self.data[key] = value

        class MockScope:
            def __init__(self):
                self._gen_ai_original_message_count = {}

        small_limit = 3000
        span = MockSpan()
        scope = MockScope()
        original_message_count = len(large_messages)

        truncated_messages = truncate_and_annotate_messages(
            large_messages, span, scope, max_bytes=small_limit
        )

        assert len(truncated_messages) < original_message_count

        assert span.span_id in scope._gen_ai_original_message_count
        stored_original_length = scope._gen_ai_original_message_count[span.span_id]
        assert stored_original_length == original_message_count

        event = {
            "spans": [
                {
                    "span_id": span.span_id,
                    "data": {SPANDATA.GEN_AI_REQUEST_MESSAGES: truncated_messages},
                }
            ]
        }

        for event_span in event["spans"]:
            span_id = event_span.get("span_id")
            span_data = event_span.get("data", {})
            if (
                span_id
                and span_id in scope._gen_ai_original_message_count
                and SPANDATA.GEN_AI_REQUEST_MESSAGES in span_data
            ):
                span_data[SPANDATA.GEN_AI_REQUEST_MESSAGES] = AnnotatedValue(
                    span_data[SPANDATA.GEN_AI_REQUEST_MESSAGES],
                    {"len": scope._gen_ai_original_message_count[span_id]},
                )

        messages_value = event["spans"][0]["data"][SPANDATA.GEN_AI_REQUEST_MESSAGES]
        assert isinstance(messages_value, AnnotatedValue)
        assert messages_value.metadata["len"] == stored_original_length
        assert len(messages_value.value) == len(truncated_messages)


class TestRedactBlobMessageParts:
    def test_redacts_single_blob_content(self):
        """Test that blob content is redacted without mutating original messages"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": "How many ponies do you see in the image?",
                        "type": "text",
                    },
                    {
                        "type": "blob",
                        "modality": "image",
                        "mime_type": "image/jpeg",
                        "content": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                    },
                ],
            }
        ]

        # Save original blob content for comparison
        original_blob_content = messages[0]["content"][1]["content"]

        result = redact_blob_message_parts(messages)

        # Original messages should be UNCHANGED
        assert messages[0]["content"][1]["content"] == original_blob_content

        # Result should have redacted content
        assert (
            result[0]["content"][0]["text"]
            == "How many ponies do you see in the image?"
        )
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][1]["type"] == "blob"
        assert result[0]["content"][1]["modality"] == "image"
        assert result[0]["content"][1]["mime_type"] == "image/jpeg"
        assert result[0]["content"][1]["content"] == BLOB_DATA_SUBSTITUTE

    def test_redacts_multiple_blob_parts(self):
        """Test that multiple blob parts are redacted without mutation"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "Compare these images", "type": "text"},
                    {
                        "type": "blob",
                        "modality": "image",
                        "mime_type": "image/jpeg",
                        "content": "data:image/jpeg;base64,first_image",
                    },
                    {
                        "type": "blob",
                        "modality": "image",
                        "mime_type": "image/png",
                        "content": "data:image/png;base64,second_image",
                    },
                ],
            }
        ]

        original_first = messages[0]["content"][1]["content"]
        original_second = messages[0]["content"][2]["content"]

        result = redact_blob_message_parts(messages)

        # Original should be unchanged
        assert messages[0]["content"][1]["content"] == original_first
        assert messages[0]["content"][2]["content"] == original_second

        # Result should be redacted
        assert result[0]["content"][0]["text"] == "Compare these images"
        assert result[0]["content"][1]["content"] == BLOB_DATA_SUBSTITUTE
        assert result[0]["content"][2]["content"] == BLOB_DATA_SUBSTITUTE

    def test_redacts_blobs_in_multiple_messages(self):
        """Test that blob parts are redacted across multiple messages without mutation"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "First message", "type": "text"},
                    {
                        "type": "blob",
                        "modality": "image",
                        "content": "data:image/jpeg;base64,first",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "I see the image.",
            },
            {
                "role": "user",
                "content": [
                    {"text": "Second message", "type": "text"},
                    {
                        "type": "blob",
                        "modality": "image",
                        "content": "data:image/jpeg;base64,second",
                    },
                ],
            },
        ]

        original_first = messages[0]["content"][1]["content"]
        original_second = messages[2]["content"][1]["content"]

        result = redact_blob_message_parts(messages)

        # Original should be unchanged
        assert messages[0]["content"][1]["content"] == original_first
        assert messages[2]["content"][1]["content"] == original_second

        # Result should be redacted
        assert result[0]["content"][1]["content"] == BLOB_DATA_SUBSTITUTE
        assert result[1]["content"] == "I see the image."  # Unchanged
        assert result[2]["content"][1]["content"] == BLOB_DATA_SUBSTITUTE

    def test_no_blobs_returns_original_list(self):
        """Test that messages without blobs are returned as-is (performance optimization)"""
        messages = [
            {"role": "user", "content": "Simple text message"},
            {"role": "assistant", "content": "Simple response"},
        ]

        result = redact_blob_message_parts(messages)

        # Should return the same list object when no blobs present
        assert result is messages

    def test_handles_non_dict_messages(self):
        """Test that non-dict messages are handled gracefully"""
        messages = [
            "string message",
            {"role": "user", "content": "text"},
            None,
            123,
        ]

        result = redact_blob_message_parts(messages)

        # Should return same list since no blobs
        assert result is messages

    def test_handles_non_dict_content_items(self):
        """Test that non-dict content items in arrays are handled"""
        messages = [
            {
                "role": "user",
                "content": [
                    "string item",
                    {"text": "text item", "type": "text"},
                    None,
                ],
            }
        ]

        result = redact_blob_message_parts(messages)

        # Should return same list since no blobs
        assert result is messages


class TestParseDataUri:
    def test_parses_base64_image_data_uri(self):
        """Test parsing a standard base64-encoded image data URI"""
        uri = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
        mime_type, content = parse_data_uri(uri)

        assert mime_type == "image/jpeg"
        assert content == "/9j/4AAQSkZJRg=="

    def test_parses_png_data_uri(self):
        """Test parsing a PNG image data URI"""
        uri = "data:image/png;base64,iVBORw0KGgo="
        mime_type, content = parse_data_uri(uri)

        assert mime_type == "image/png"
        assert content == "iVBORw0KGgo="

    def test_parses_plain_text_data_uri(self):
        """Test parsing a plain text data URI without base64 encoding"""
        uri = "data:text/plain,Hello World"
        mime_type, content = parse_data_uri(uri)

        assert mime_type == "text/plain"
        assert content == "Hello World"

    def test_parses_data_uri_with_empty_mime_type(self):
        """Test parsing a data URI with empty mime type"""
        uri = "data:;base64,SGVsbG8="
        mime_type, content = parse_data_uri(uri)

        assert mime_type == ""
        assert content == "SGVsbG8="

    def test_parses_data_uri_with_only_data_prefix(self):
        """Test parsing a data URI with only the data: prefix and content"""
        uri = "data:,Hello"
        mime_type, content = parse_data_uri(uri)

        assert mime_type == ""
        assert content == "Hello"

    def test_raises_on_missing_comma(self):
        """Test that ValueError is raised when comma separator is missing"""
        with pytest.raises(ValueError, match="missing comma separator"):
            parse_data_uri("data:image/jpeg;base64")

    def test_raises_on_empty_string(self):
        """Test that ValueError is raised for empty string"""
        with pytest.raises(ValueError, match="missing comma separator"):
            parse_data_uri("")

    def test_handles_content_with_commas(self):
        """Test that only the first comma is used as separator"""
        uri = "data:text/plain,Hello,World,With,Commas"
        mime_type, content = parse_data_uri(uri)

        assert mime_type == "text/plain"
        assert content == "Hello,World,With,Commas"

    def test_parses_data_uri_with_multiple_parameters(self):
        """Test parsing a data URI with multiple parameters in header"""
        uri = "data:text/plain;charset=utf-8;base64,SGVsbG8="
        mime_type, content = parse_data_uri(uri)

        assert mime_type == "text/plain"
        assert content == "SGVsbG8="

    def test_parses_audio_data_uri(self):
        """Test parsing an audio data URI"""
        uri = "data:audio/wav;base64,UklGRiQA"
        mime_type, content = parse_data_uri(uri)

        assert mime_type == "audio/wav"
        assert content == "UklGRiQA"

    def test_handles_uri_without_data_prefix(self):
        """Test parsing a URI that doesn't have the data: prefix"""
        uri = "image/jpeg;base64,/9j/4AAQ"
        mime_type, content = parse_data_uri(uri)

        assert mime_type == "image/jpeg"
        assert content == "/9j/4AAQ"


class TestGetModalityFromMimeType:
    def test_image_mime_types(self):
        """Test that image MIME types return 'image' modality"""
        assert get_modality_from_mime_type("image/jpeg") == "image"
        assert get_modality_from_mime_type("image/png") == "image"
        assert get_modality_from_mime_type("image/gif") == "image"
        assert get_modality_from_mime_type("image/webp") == "image"
        assert get_modality_from_mime_type("IMAGE/JPEG") == "image"  # case insensitive

    def test_audio_mime_types(self):
        """Test that audio MIME types return 'audio' modality"""
        assert get_modality_from_mime_type("audio/mp3") == "audio"
        assert get_modality_from_mime_type("audio/wav") == "audio"
        assert get_modality_from_mime_type("audio/ogg") == "audio"
        assert get_modality_from_mime_type("AUDIO/MP3") == "audio"  # case insensitive

    def test_video_mime_types(self):
        """Test that video MIME types return 'video' modality"""
        assert get_modality_from_mime_type("video/mp4") == "video"
        assert get_modality_from_mime_type("video/webm") == "video"
        assert get_modality_from_mime_type("video/quicktime") == "video"
        assert get_modality_from_mime_type("VIDEO/MP4") == "video"  # case insensitive

    def test_document_mime_types(self):
        """Test that application and text MIME types return 'document' modality"""
        assert get_modality_from_mime_type("application/pdf") == "document"
        assert get_modality_from_mime_type("application/json") == "document"
        assert get_modality_from_mime_type("text/plain") == "document"
        assert get_modality_from_mime_type("text/html") == "document"

    def test_empty_mime_type_returns_image(self):
        """Test that empty MIME type defaults to 'image'"""
        assert get_modality_from_mime_type("") == "image"

    def test_none_mime_type_returns_image(self):
        """Test that None-like values default to 'image'"""
        assert get_modality_from_mime_type(None) == "image"

    def test_unknown_mime_type_returns_image(self):
        """Test that unknown MIME types default to 'image'"""
        assert get_modality_from_mime_type("unknown/type") == "image"
        assert get_modality_from_mime_type("custom/format") == "image"


class TestTransformOpenAIContentPart:
    """Tests for the OpenAI-specific transform function."""

    def test_image_url_with_data_uri(self):
        """Test transforming OpenAI image_url with base64 data URI"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="},
        }
        result = transform_openai_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRg==",
        }

    def test_image_url_with_regular_url(self):
        """Test transforming OpenAI image_url with regular URL"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.jpg"},
        }
        result = transform_openai_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "https://example.com/image.jpg",
        }

    def test_image_url_string_format(self):
        """Test transforming OpenAI image_url where image_url is a string"""
        content_part = {
            "type": "image_url",
            "image_url": "https://example.com/image.jpg",
        }
        result = transform_openai_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "https://example.com/image.jpg",
        }

    def test_image_url_invalid_data_uri(self):
        """Test transforming OpenAI image_url with invalid data URI falls back to URI"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64"},  # Missing comma
        }
        result = transform_openai_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "data:image/jpeg;base64",
        }

    def test_empty_url_returns_none(self):
        """Test that image_url with empty URL returns None"""
        content_part = {"type": "image_url", "image_url": {"url": ""}}
        assert transform_openai_content_part(content_part) is None

    def test_non_image_url_type_returns_none(self):
        """Test that non-image_url types return None"""
        content_part = {"type": "text", "text": "Hello"}
        assert transform_openai_content_part(content_part) is None

    def test_anthropic_format_returns_none(self):
        """Test that Anthropic format returns None (not handled)"""
        content_part = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
        }
        assert transform_openai_content_part(content_part) is None

    def test_google_format_returns_none(self):
        """Test that Google format returns None (not handled)"""
        content_part = {"inline_data": {"mime_type": "image/jpeg", "data": "abc"}}
        assert transform_openai_content_part(content_part) is None

    def test_non_dict_returns_none(self):
        """Test that non-dict input returns None"""
        assert transform_openai_content_part("string") is None
        assert transform_openai_content_part(123) is None
        assert transform_openai_content_part(None) is None


class TestTransformAnthropicContentPart:
    """Tests for the Anthropic-specific transform function."""

    def test_image_base64(self):
        """Test transforming Anthropic image with base64 source"""
        content_part = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "iVBORw0KGgo=",
            },
        }
        result = transform_anthropic_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/png",
            "content": "iVBORw0KGgo=",
        }

    def test_image_url(self):
        """Test transforming Anthropic image with URL source"""
        content_part = {
            "type": "image",
            "source": {
                "type": "url",
                "media_type": "image/jpeg",
                "url": "https://example.com/image.jpg",
            },
        }
        result = transform_anthropic_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "image/jpeg",
            "uri": "https://example.com/image.jpg",
        }

    def test_image_file(self):
        """Test transforming Anthropic image with file source"""
        content_part = {
            "type": "image",
            "source": {
                "type": "file",
                "media_type": "image/jpeg",
                "file_id": "file_123",
            },
        }
        result = transform_anthropic_content_part(content_part)

        assert result == {
            "type": "file",
            "modality": "image",
            "mime_type": "image/jpeg",
            "file_id": "file_123",
        }

    def test_document_base64(self):
        """Test transforming Anthropic document with base64 source"""
        content_part = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": "JVBERi0xLjQ=",
            },
        }
        result = transform_anthropic_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "document",
            "mime_type": "application/pdf",
            "content": "JVBERi0xLjQ=",
        }

    def test_document_url(self):
        """Test transforming Anthropic document with URL source"""
        content_part = {
            "type": "document",
            "source": {
                "type": "url",
                "media_type": "application/pdf",
                "url": "https://example.com/doc.pdf",
            },
        }
        result = transform_anthropic_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "document",
            "mime_type": "application/pdf",
            "uri": "https://example.com/doc.pdf",
        }

    def test_invalid_source_returns_none(self):
        """Test that Anthropic format with invalid source returns None"""
        content_part = {"type": "image", "source": "not_a_dict"}
        assert transform_anthropic_content_part(content_part) is None

    def test_unknown_source_type_returns_none(self):
        """Test that Anthropic format with unknown source type returns None"""
        content_part = {
            "type": "image",
            "source": {"type": "unknown", "data": "something"},
        }
        assert transform_anthropic_content_part(content_part) is None

    def test_missing_source_returns_none(self):
        """Test that Anthropic format without source returns None"""
        content_part = {"type": "image", "data": "something"}
        assert transform_anthropic_content_part(content_part) is None

    def test_openai_format_returns_none(self):
        """Test that OpenAI format returns None (not handled)"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "https://example.com"},
        }
        assert transform_anthropic_content_part(content_part) is None

    def test_google_format_returns_none(self):
        """Test that Google format returns None (not handled)"""
        content_part = {"inline_data": {"mime_type": "image/jpeg", "data": "abc"}}
        assert transform_anthropic_content_part(content_part) is None

    def test_non_dict_returns_none(self):
        """Test that non-dict input returns None"""
        assert transform_anthropic_content_part("string") is None
        assert transform_anthropic_content_part(123) is None
        assert transform_anthropic_content_part(None) is None


class TestTransformGoogleContentPart:
    """Tests for the Google GenAI-specific transform function."""

    def test_inline_data(self):
        """Test transforming Google inline_data format"""
        content_part = {
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": "/9j/4AAQSkZJRg==",
            }
        }
        result = transform_google_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRg==",
        }

    def test_file_data(self):
        """Test transforming Google file_data format"""
        content_part = {
            "file_data": {
                "mime_type": "video/mp4",
                "file_uri": "gs://bucket/video.mp4",
            }
        }
        result = transform_google_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "video",
            "mime_type": "video/mp4",
            "uri": "gs://bucket/video.mp4",
        }

    def test_inline_data_audio(self):
        """Test transforming Google inline_data with audio"""
        content_part = {
            "inline_data": {
                "mime_type": "audio/wav",
                "data": "UklGRiQA",
            }
        }
        result = transform_google_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "audio",
            "mime_type": "audio/wav",
            "content": "UklGRiQA",
        }

    def test_inline_data_not_dict_returns_none(self):
        """Test that Google inline_data with non-dict value returns None"""
        content_part = {"inline_data": "not_a_dict"}
        assert transform_google_content_part(content_part) is None

    def test_file_data_not_dict_returns_none(self):
        """Test that Google file_data with non-dict value returns None"""
        content_part = {"file_data": "not_a_dict"}
        assert transform_google_content_part(content_part) is None

    def test_openai_format_returns_none(self):
        """Test that OpenAI format returns None (not handled)"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "https://example.com"},
        }
        assert transform_google_content_part(content_part) is None

    def test_anthropic_format_returns_none(self):
        """Test that Anthropic format returns None (not handled)"""
        content_part = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
        }
        assert transform_google_content_part(content_part) is None

    def test_non_dict_returns_none(self):
        """Test that non-dict input returns None"""
        assert transform_google_content_part("string") is None
        assert transform_google_content_part(123) is None
        assert transform_google_content_part(None) is None


class TestTransformGenericContentPart:
    """Tests for the generic/LangChain-style transform function."""

    def test_image_base64(self):
        """Test transforming generic format with base64"""
        content_part = {
            "type": "image",
            "base64": "/9j/4AAQSkZJRg==",
            "mime_type": "image/jpeg",
        }
        result = transform_generic_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRg==",
        }

    def test_audio_url(self):
        """Test transforming generic format with URL"""
        content_part = {
            "type": "audio",
            "url": "https://example.com/audio.mp3",
            "mime_type": "audio/mp3",
        }
        result = transform_generic_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "audio",
            "mime_type": "audio/mp3",
            "uri": "https://example.com/audio.mp3",
        }

    def test_file_with_file_id(self):
        """Test transforming generic format with file_id"""
        content_part = {
            "type": "file",
            "file_id": "file_456",
            "mime_type": "application/pdf",
        }
        result = transform_generic_content_part(content_part)

        assert result == {
            "type": "file",
            "modality": "document",
            "mime_type": "application/pdf",
            "file_id": "file_456",
        }

    def test_video_base64(self):
        """Test transforming generic video format"""
        content_part = {
            "type": "video",
            "base64": "AAAA",
            "mime_type": "video/mp4",
        }
        result = transform_generic_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "video",
            "mime_type": "video/mp4",
            "content": "AAAA",
        }

    def test_image_with_source_returns_none(self):
        """Test that image with source key (Anthropic style) returns None"""
        # This is Anthropic format, should NOT be handled by generic
        content_part = {
            "type": "image",
            "source": {"type": "base64", "data": "abc"},
        }
        assert transform_generic_content_part(content_part) is None

    def test_text_type_returns_none(self):
        """Test that text type returns None"""
        content_part = {"type": "text", "text": "Hello"}
        assert transform_generic_content_part(content_part) is None

    def test_openai_format_returns_none(self):
        """Test that OpenAI format returns None (not handled)"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "https://example.com"},
        }
        assert transform_generic_content_part(content_part) is None

    def test_google_format_returns_none(self):
        """Test that Google format returns None (not handled)"""
        content_part = {"inline_data": {"mime_type": "image/jpeg", "data": "abc"}}
        assert transform_generic_content_part(content_part) is None

    def test_non_dict_returns_none(self):
        """Test that non-dict input returns None"""
        assert transform_generic_content_part("string") is None
        assert transform_generic_content_part(123) is None
        assert transform_generic_content_part(None) is None

    def test_missing_data_key_returns_none(self):
        """Test that missing data key (base64/url/file_id) returns None"""
        content_part = {"type": "image", "mime_type": "image/jpeg"}
        assert transform_generic_content_part(content_part) is None


class TestTransformContentPart:
    # OpenAI/LiteLLM format tests
    def test_openai_image_url_with_data_uri(self):
        """Test transforming OpenAI image_url with base64 data URI"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="},
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRg==",
        }

    def test_openai_image_url_with_regular_url(self):
        """Test transforming OpenAI image_url with regular URL"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.jpg"},
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "https://example.com/image.jpg",
        }

    def test_openai_image_url_string_format(self):
        """Test transforming OpenAI image_url where image_url is a string"""
        content_part = {
            "type": "image_url",
            "image_url": "https://example.com/image.jpg",
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "https://example.com/image.jpg",
        }

    def test_openai_image_url_invalid_data_uri(self):
        """Test transforming OpenAI image_url with invalid data URI falls back to URI"""
        content_part = {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64"},  # Missing comma
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "data:image/jpeg;base64",
        }

    # Anthropic format tests
    def test_anthropic_image_base64(self):
        """Test transforming Anthropic image with base64 source"""
        content_part = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "iVBORw0KGgo=",
            },
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/png",
            "content": "iVBORw0KGgo=",
        }

    def test_anthropic_image_url(self):
        """Test transforming Anthropic image with URL source"""
        content_part = {
            "type": "image",
            "source": {
                "type": "url",
                "media_type": "image/jpeg",
                "url": "https://example.com/image.jpg",
            },
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "image",
            "mime_type": "image/jpeg",
            "uri": "https://example.com/image.jpg",
        }

    def test_anthropic_image_file(self):
        """Test transforming Anthropic image with file source"""
        content_part = {
            "type": "image",
            "source": {
                "type": "file",
                "media_type": "image/jpeg",
                "file_id": "file_123",
            },
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "file",
            "modality": "image",
            "mime_type": "image/jpeg",
            "file_id": "file_123",
        }

    def test_anthropic_document_base64(self):
        """Test transforming Anthropic document with base64 source"""
        content_part = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": "JVBERi0xLjQ=",
            },
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "document",
            "mime_type": "application/pdf",
            "content": "JVBERi0xLjQ=",
        }

    def test_anthropic_document_url(self):
        """Test transforming Anthropic document with URL source"""
        content_part = {
            "type": "document",
            "source": {
                "type": "url",
                "media_type": "application/pdf",
                "url": "https://example.com/doc.pdf",
            },
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "document",
            "mime_type": "application/pdf",
            "uri": "https://example.com/doc.pdf",
        }

    # Google format tests
    def test_google_inline_data(self):
        """Test transforming Google inline_data format"""
        content_part = {
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": "/9j/4AAQSkZJRg==",
            }
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRg==",
        }

    def test_google_file_data(self):
        """Test transforming Google file_data format"""
        content_part = {
            "file_data": {
                "mime_type": "video/mp4",
                "file_uri": "gs://bucket/video.mp4",
            }
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "video",
            "mime_type": "video/mp4",
            "uri": "gs://bucket/video.mp4",
        }

    def test_google_inline_data_audio(self):
        """Test transforming Google inline_data with audio"""
        content_part = {
            "inline_data": {
                "mime_type": "audio/wav",
                "data": "UklGRiQA",
            }
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "audio",
            "mime_type": "audio/wav",
            "content": "UklGRiQA",
        }

    # Generic format tests (LangChain style)
    def test_generic_image_base64(self):
        """Test transforming generic format with base64"""
        content_part = {
            "type": "image",
            "base64": "/9j/4AAQSkZJRg==",
            "mime_type": "image/jpeg",
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQSkZJRg==",
        }

    def test_generic_audio_url(self):
        """Test transforming generic format with URL"""
        content_part = {
            "type": "audio",
            "url": "https://example.com/audio.mp3",
            "mime_type": "audio/mp3",
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "uri",
            "modality": "audio",
            "mime_type": "audio/mp3",
            "uri": "https://example.com/audio.mp3",
        }

    def test_generic_file_with_file_id(self):
        """Test transforming generic format with file_id"""
        content_part = {
            "type": "file",
            "file_id": "file_456",
            "mime_type": "application/pdf",
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "file",
            "modality": "document",
            "mime_type": "application/pdf",
            "file_id": "file_456",
        }

    def test_generic_video_base64(self):
        """Test transforming generic video format"""
        content_part = {
            "type": "video",
            "base64": "AAAA",
            "mime_type": "video/mp4",
        }
        result = transform_content_part(content_part)

        assert result == {
            "type": "blob",
            "modality": "video",
            "mime_type": "video/mp4",
            "content": "AAAA",
        }

    # Edge cases and error handling
    def test_text_block_returns_none(self):
        """Test that text blocks return None (not transformed)"""
        content_part = {"type": "text", "text": "Hello world"}
        result = transform_content_part(content_part)

        assert result is None

    def test_non_dict_returns_none(self):
        """Test that non-dict input returns None"""
        assert transform_content_part("string") is None
        assert transform_content_part(123) is None
        assert transform_content_part(None) is None
        assert transform_content_part([1, 2, 3]) is None

    def test_empty_dict_returns_none(self):
        """Test that empty dict returns None"""
        assert transform_content_part({}) is None

    def test_unknown_type_returns_none(self):
        """Test that unknown type returns None"""
        content_part = {"type": "unknown", "data": "something"}
        assert transform_content_part(content_part) is None

    def test_openai_image_url_empty_url_returns_none(self):
        """Test that image_url with empty URL returns None"""
        content_part = {"type": "image_url", "image_url": {"url": ""}}
        assert transform_content_part(content_part) is None

    def test_anthropic_invalid_source_returns_none(self):
        """Test that Anthropic format with invalid source returns None"""
        content_part = {"type": "image", "source": "not_a_dict"}
        assert transform_content_part(content_part) is None

    def test_anthropic_unknown_source_type_returns_none(self):
        """Test that Anthropic format with unknown source type returns None"""
        content_part = {
            "type": "image",
            "source": {"type": "unknown", "data": "something"},
        }
        assert transform_content_part(content_part) is None

    def test_google_inline_data_not_dict_returns_none(self):
        """Test that Google inline_data with non-dict value returns None"""
        content_part = {"inline_data": "not_a_dict"}
        assert transform_content_part(content_part) is None

    def test_google_file_data_not_dict_returns_none(self):
        """Test that Google file_data with non-dict value returns None"""
        content_part = {"file_data": "not_a_dict"}
        assert transform_content_part(content_part) is None


class TestTransformMessageContent:
    def test_string_content_returned_as_is(self):
        """Test that string content is returned unchanged"""
        content = "Hello, world!"
        result = transform_message_content(content)

        assert result == "Hello, world!"

    def test_list_with_transformable_items(self):
        """Test transforming a list with transformable content parts"""
        content = [
            {"type": "text", "text": "What's in this image?"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ"},
            },
        ]
        result = transform_message_content(content)

        assert len(result) == 2
        # Text block should be unchanged (transform returns None, so original kept)
        assert result[0] == {"type": "text", "text": "What's in this image?"}
        # Image should be transformed
        assert result[1] == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQ",
        }

    def test_list_with_non_dict_items(self):
        """Test that non-dict items in list are kept as-is"""
        content = ["text string", 123, {"type": "text", "text": "hi"}]
        result = transform_message_content(content)

        assert result == ["text string", 123, {"type": "text", "text": "hi"}]

    def test_tuple_content(self):
        """Test that tuple content is also handled"""
        content = (
            {"type": "text", "text": "Hello"},
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/img.jpg"},
            },
        )
        result = transform_message_content(content)

        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "Hello"}
        assert result[1] == {
            "type": "uri",
            "modality": "image",
            "mime_type": "",
            "uri": "https://example.com/img.jpg",
        }

    def test_other_types_returned_as_is(self):
        """Test that other types are returned unchanged"""
        assert transform_message_content(123) == 123
        assert transform_message_content(None) is None
        assert transform_message_content({"key": "value"}) == {"key": "value"}

    def test_mixed_content_types(self):
        """Test transforming mixed content with multiple formats"""
        content = [
            {"type": "text", "text": "Look at these:"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,iVBORw0"},
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": "/9j/4AAQ",
                },
            },
            {"inline_data": {"mime_type": "audio/wav", "data": "UklGRiQA"}},
        ]
        result = transform_message_content(content)

        assert len(result) == 4
        assert result[0] == {"type": "text", "text": "Look at these:"}
        assert result[1] == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/png",
            "content": "iVBORw0",
        }
        assert result[2] == {
            "type": "blob",
            "modality": "image",
            "mime_type": "image/jpeg",
            "content": "/9j/4AAQ",
        }
        assert result[3] == {
            "type": "blob",
            "modality": "audio",
            "mime_type": "audio/wav",
            "content": "UklGRiQA",
        }

    def test_empty_list(self):
        """Test that empty list is returned as empty list"""
        assert transform_message_content([]) == []

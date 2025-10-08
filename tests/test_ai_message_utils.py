import json
import pytest

from sentry_sdk.ai.message_utils import (
    MAX_GEN_AI_MESSAGE_BYTES,
    truncate_messages_by_size,
    serialize_gen_ai_messages,
    get_messages_metadata,
    truncate_and_serialize_messages,
)
from sentry_sdk._types import AnnotatedValue
from sentry_sdk.serializer import serialize


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
    large_content = "This is a very long message. " * 1000  # ~30KB per message
    return [
        {"role": "system", "content": large_content},
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": large_content},
        {"role": "user", "content": large_content},
    ]


class TestTruncateMessagesBySize:
    def test_no_truncation_needed(self, sample_messages):
        """Test that messages under the limit are not truncated"""
        result = truncate_messages_by_size(
            sample_messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES
        )
        assert len(result) == len(sample_messages)
        assert result == sample_messages

    def test_truncation_removes_oldest_first(self, large_messages):
        """Test that oldest messages are removed first during truncation"""
        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 100  # 5KB limit to force truncation
        result = truncate_messages_by_size(large_messages, max_bytes=small_limit)

        # Should have fewer messages
        assert len(result) < len(large_messages)

        # Should keep the most recent messages
        # The last message should always be preserved if possible
        if result:
            assert result[-1] == large_messages[-1]

    def test_empty_messages_list(self):
        """Test handling of empty messages list"""
        result = truncate_messages_by_size(
            [], max_bytes=MAX_GEN_AI_MESSAGE_BYTES // 500
        )
        assert result == []

    def test_single_message_under_limit(self):
        """Test single message under size limit"""
        messages = [{"role": "user", "content": "Hello!"}]
        result = truncate_messages_by_size(
            messages, max_bytes=MAX_GEN_AI_MESSAGE_BYTES // 500
        )
        assert result == messages

    def test_single_message_over_limit(self):
        """Test single message that exceeds size limit"""
        large_content = "x" * 10000
        messages = [{"role": "user", "content": large_content}]
        result = truncate_messages_by_size(messages, max_bytes=100)  # Very small limit

        # Should return empty list if even single message is too large
        assert result == []

    def test_progressive_truncation(self, large_messages):
        """Test that truncation works progressively with different limits"""
        # Test different size limits based on the constant
        limits = [
            MAX_GEN_AI_MESSAGE_BYTES // 5,  # 100KB
            MAX_GEN_AI_MESSAGE_BYTES // 10,  # 50KB
            MAX_GEN_AI_MESSAGE_BYTES // 25,  # 20KB
            MAX_GEN_AI_MESSAGE_BYTES // 100,  # 5KB
            MAX_GEN_AI_MESSAGE_BYTES // 500,  # 1KB
        ]
        prev_count = len(large_messages)

        for limit in limits:
            result = truncate_messages_by_size(large_messages, max_bytes=limit)
            current_count = len(result)

            # As limit decreases, message count should not increase
            assert current_count <= prev_count
            prev_count = current_count

    def test_exact_size_boundary(self):
        """Test behavior at exact size boundaries"""
        # Create a message that serializes to a known size
        messages = [{"role": "user", "content": "test"}]

        # Get the exact serialized size
        from sentry_sdk.ai.message_utils import serialize

        serialized = serialize(messages, is_vars=False)
        json_str = json.dumps(serialized, separators=(",", ":"))
        exact_size = len(json_str.encode("utf-8"))

        # Should keep the message at exact size
        result = truncate_messages_by_size(messages, max_bytes=exact_size)
        assert len(result) == 1

        # Should remove the message if limit is one byte smaller
        result = truncate_messages_by_size(messages, max_bytes=exact_size - 1)
        assert len(result) == 0


class TestSerializeGenAiMessages:
    def test_serialize_normal_messages(self, sample_messages):
        """Test serialization of normal messages"""
        result = serialize_gen_ai_messages(sample_messages)

        assert result is not None
        assert isinstance(result, str)

        # Should be valid JSON
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) <= len(sample_messages)  # Could be truncated

    def test_serialize_none_messages(self):
        """Test serialization of None input"""
        result = serialize_gen_ai_messages(None)
        assert result is None

    def test_serialize_empty_messages(self):
        """Test serialization of empty list"""
        result = serialize_gen_ai_messages([])
        assert result is None

    def test_serialize_with_truncation(self, large_messages):
        """Test serialization with size-based truncation"""
        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 100  # 5KB limit to force truncation
        result = serialize_gen_ai_messages(large_messages, max_bytes=small_limit)

        if result:  # Might be None if all messages are too large
            assert isinstance(result, str)

            # Verify the result is under the size limit
            result_size = len(result.encode("utf-8"))
            assert result_size <= small_limit

            # Should be valid JSON
            parsed = json.loads(result)
            assert isinstance(parsed, list)

    def test_serialize_preserves_message_structure(self):
        """Test that serialization preserves message structure"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        result = serialize_gen_ai_messages(messages)
        parsed = json.loads(result)

        assert len(parsed) == 2
        assert parsed[0]["role"] == "user"
        assert parsed[0]["content"] == "Hello"
        assert parsed[1]["role"] == "assistant"
        assert parsed[1]["content"] == "Hi there!"


class TestGetMessagesMetadata:
    def test_no_truncation_metadata(self, sample_messages):
        """Test metadata when no truncation occurs"""
        metadata = get_messages_metadata(sample_messages, sample_messages)

        assert metadata["original_count"] == len(sample_messages)
        assert metadata["truncated_count"] == len(sample_messages)
        assert metadata["messages_removed"] == 0
        assert metadata["was_truncated"] is False

    def test_truncation_metadata(self, sample_messages):
        """Test metadata when truncation occurs"""
        truncated = sample_messages[2:]  # Remove first 2 messages
        metadata = get_messages_metadata(sample_messages, truncated)

        assert metadata["original_count"] == len(sample_messages)
        assert metadata["truncated_count"] == len(truncated)
        assert metadata["messages_removed"] == 2
        assert metadata["was_truncated"] is True

    def test_empty_lists_metadata(self):
        """Test metadata with empty lists"""
        metadata = get_messages_metadata([], [])

        assert metadata["original_count"] == 0
        assert metadata["truncated_count"] == 0
        assert metadata["messages_removed"] == 0
        assert metadata["was_truncated"] is False

    def test_none_input_metadata(self):
        """Test metadata with None inputs"""
        metadata = get_messages_metadata(None, None)

        assert metadata["original_count"] == 0
        assert metadata["truncated_count"] == 0
        assert metadata["messages_removed"] == 0
        assert metadata["was_truncated"] is False

    def test_complete_truncation_metadata(self, sample_messages):
        """Test metadata when all messages are removed"""
        metadata = get_messages_metadata(sample_messages, [])

        assert metadata["original_count"] == len(sample_messages)
        assert metadata["truncated_count"] == 0
        assert metadata["messages_removed"] == len(sample_messages)
        assert metadata["was_truncated"] is True


class TestTruncateAndSerializeMessages:
    def test_main_function_with_normal_messages(self, sample_messages):
        """Test the main function with normal messages"""
        result = truncate_and_serialize_messages(sample_messages)

        assert "serialized_data" in result
        assert "metadata" in result
        assert "original_size" in result

        assert result["serialized_data"] is not None
        assert isinstance(result["serialized_data"], str)
        assert result["original_size"] > 0
        assert result["metadata"]["was_truncated"] is False

    def test_main_function_with_large_messages(self, large_messages):
        """Test the main function with messages requiring truncation"""
        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 100  # 5KB limit to force truncation
        result = truncate_and_serialize_messages(large_messages, max_bytes=small_limit)

        assert "serialized_data" in result
        assert "metadata" in result
        assert "original_size" in result

        # Original size should be large
        assert result["original_size"] > small_limit

        # May or may not be truncated depending on how large the messages are
        if result["serialized_data"]:
            serialized_size = len(result["serialized_data"].encode("utf-8"))
            assert serialized_size <= small_limit

    def test_main_function_with_none_input(self):
        """Test the main function with None input"""
        result = truncate_and_serialize_messages(None)

        assert result["serialized_data"] is None
        assert result["original_size"] == 0
        assert result["metadata"]["was_truncated"] is False

    def test_main_function_with_empty_input(self):
        """Test the main function with empty input"""
        result = truncate_and_serialize_messages([])

        assert result["serialized_data"] is None
        assert result["original_size"] == 0
        assert result["metadata"]["was_truncated"] is False

    def test_main_function_size_comparison(self, sample_messages):
        """Test that serialized data is smaller than or equal to original"""
        result = truncate_and_serialize_messages(sample_messages)

        if result["serialized_data"]:
            serialized_size = len(result["serialized_data"].encode("utf-8"))
            # Serialized size should be <= original size (could be equal if no truncation)
            assert serialized_size <= result["original_size"]

    def test_main_function_respects_custom_limit(self, large_messages):
        """Test that the main function respects custom byte limits"""
        custom_limit = MAX_GEN_AI_MESSAGE_BYTES // 250  # 2KB limit
        result = truncate_and_serialize_messages(large_messages, max_bytes=custom_limit)

        if result["serialized_data"]:
            serialized_size = len(result["serialized_data"].encode("utf-8"))
            assert serialized_size <= custom_limit

    def test_main_function_default_limit(self, sample_messages):
        """Test that the main function uses the default limit correctly"""
        result = truncate_and_serialize_messages(sample_messages)

        # With normal sample messages, should not need truncation
        assert result["metadata"]["was_truncated"] is False
        assert result["serialized_data"] is not None


class TestConstants:
    def test_default_limit_is_reasonable(self):
        """Test that the default limit is reasonable"""
        assert MAX_GEN_AI_MESSAGE_BYTES > 0
        assert MAX_GEN_AI_MESSAGE_BYTES < 10**6  # Should be less than MAX_EVENT_BYTES


class TestEdgeCases:
    def test_messages_with_special_characters(self):
        """Test messages containing special characters"""
        messages = [
            {"role": "user", "content": "Hello 🌍! How are you? 中文测试"},
            {
                "role": "assistant",
                "content": "I'm doing well! Unicode: ñáéíóú àèìòù äöü",
            },
        ]

        result = truncate_and_serialize_messages(messages)
        assert result["serialized_data"] is not None

        # Should be valid JSON
        parsed = json.loads(result["serialized_data"])
        assert len(parsed) == 2
        assert "🌍" in parsed[0]["content"]

    def test_messages_with_nested_structures(self):
        """Test messages with complex nested structures"""
        messages = [
            {
                "role": "user",
                "content": "Hello",
                "metadata": {"timestamp": "2023-01-01", "user_id": 123},
            },
            {
                "role": "assistant",
                "content": "Hi!",
                "tool_calls": [{"name": "search", "args": {"query": "test"}}],
            },
        ]

        result = truncate_and_serialize_messages(messages)
        assert result["serialized_data"] is not None

        # Should preserve the structure
        parsed = json.loads(result["serialized_data"])
        assert "metadata" in parsed[0]
        assert "tool_calls" in parsed[1]

    def test_very_small_limit(self, sample_messages):
        """Test behavior with extremely small size limit"""
        tiny_limit = 10  # 10 bytes - extremely small limit
        result = truncate_and_serialize_messages(sample_messages, max_bytes=tiny_limit)

        # With such a small limit, likely all messages will be removed
        if result["serialized_data"] is None:
            assert result["metadata"]["truncated_count"] == 0
        else:
            # If any data remains, it should be under the limit
            size = len(result["serialized_data"].encode("utf-8"))
            assert size <= tiny_limit

    def test_messages_with_none_values(self):
        """Test messages containing None values"""
        messages = [
            {"role": "user", "content": None},
            {"role": "assistant", "content": "Hello", "extra": None},
        ]

        result = truncate_and_serialize_messages(messages)
        assert result["serialized_data"] is not None

        # Should handle None values gracefully
        parsed = json.loads(result["serialized_data"])
        assert len(parsed) == 2

    def test_truncation_keeps_most_recent(self):
        """Test that truncation prioritizes keeping the most recent messages"""
        messages = []
        for i in range(10):
            messages.append(
                {
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"Message {i} with unique content that makes it identifiable",
                }
            )

        # Truncate to a small size that should remove several messages
        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 500  # 1KB limit to force truncation
        result = truncate_and_serialize_messages(messages, max_bytes=small_limit)

        if result["serialized_data"]:
            parsed = json.loads(result["serialized_data"])
            if parsed:
                # The last remaining message should be from the end of the original list
                last_kept_content = parsed[-1]["content"]
                assert (
                    "Message 9" in last_kept_content or "Message 8" in last_kept_content
                )


class TestMetaSupport:
    """Test that _meta entries are created correctly when truncation occurs"""

    def test_annotated_value_returned_on_truncation(self, large_messages):
        """Test that truncate_and_serialize_messages returns AnnotatedValue when truncation occurs"""
        # Force truncation with a limit that will keep at least one message
        # Each large message is ~30KB, so 50KB should keep 1-2 messages but force truncation
        small_limit = 50_000  # 50KB to force truncation but keep some messages
        result = truncate_and_serialize_messages(large_messages, max_bytes=small_limit)

        # Should return an AnnotatedValue when truncation occurs
        assert isinstance(result, AnnotatedValue)
        assert result.metadata == {"len": len(large_messages)}

        # The value should be the truncated messages
        assert isinstance(result.value, list)
        assert len(result.value) < len(large_messages)

    def test_no_annotated_value_when_no_truncation(self, sample_messages):
        """Test that truncate_and_serialize_messages returns plain list when no truncation occurs"""
        result = truncate_and_serialize_messages(sample_messages)

        # Should return plain list when no truncation occurs
        assert not isinstance(result, AnnotatedValue)
        assert isinstance(result, list)
        assert len(result) == len(sample_messages)
        assert result == sample_messages

    def test_meta_structure_in_serialized_output(self, large_messages):
        """Test that _meta structure is created correctly in serialized output"""
        # Force truncation with a limit that will keep at least one message
        small_limit = 50_000  # 50KB to force truncation but keep some messages
        annotated_messages = truncate_and_serialize_messages(
            large_messages, max_bytes=small_limit
        )

        # Simulate how the serializer would process this (like it does in actual span data)
        test_data = {"gen_ai": {"request": {"messages": annotated_messages}}}

        # Serialize using Sentry's serializer (which processes AnnotatedValue)
        serialized = serialize(test_data, is_vars=False)

        # Check that _meta structure was created
        assert "_meta" in serialized
        assert "gen_ai" in serialized["_meta"]
        assert "request" in serialized["_meta"]["gen_ai"]
        assert "messages" in serialized["_meta"]["gen_ai"]["request"]
        assert serialized["_meta"]["gen_ai"]["request"]["messages"][""] == {
            "len": len(large_messages)
        }

        # Check that the actual data is still there
        assert "gen_ai" in serialized
        assert "request" in serialized["gen_ai"]
        assert "messages" in serialized["gen_ai"]["request"]
        assert isinstance(serialized["gen_ai"]["request"]["messages"], list)
        assert len(serialized["gen_ai"]["request"]["messages"]) < len(large_messages)

    def test_serialize_gen_ai_messages_handles_annotated_value(self, large_messages):
        """Test that serialize_gen_ai_messages handles AnnotatedValue input correctly"""
        # Create an AnnotatedValue manually
        truncated = large_messages[:2]  # Keep only first 2 messages
        annotated = AnnotatedValue(
            value=truncated, metadata={"len": len(large_messages)}
        )

        # serialize_gen_ai_messages should handle it
        result = serialize_gen_ai_messages(annotated)

        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2  # Only 2 messages kept

    def test_empty_messages_no_annotated_value(self):
        """Test that empty messages don't create AnnotatedValue"""
        result = truncate_and_serialize_messages([])
        assert result is None

        result = truncate_and_serialize_messages(None)
        assert result is None

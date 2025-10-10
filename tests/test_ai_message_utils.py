import json
import pytest

from sentry_sdk.ai.utils import (
    MAX_GEN_AI_MESSAGE_BYTES,
    truncate_messages_by_size,
    serialize_gen_ai_messages,
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
    large_content = "This is a very long message. " * 1000
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
        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 100
        result = truncate_messages_by_size(large_messages, max_bytes=small_limit)
        assert len(result) < len(large_messages)

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

        result = truncate_messages_by_size(messages, max_bytes=100)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["content"]) < len(large_content)

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

    def test_exact_size_boundary(self):
        """Test behavior at exact size boundaries"""
        messages = [{"role": "user", "content": "test"}]

        serialized = serialize(messages, is_vars=False)
        json_str = json.dumps(serialized, separators=(",", ":"))
        exact_size = len(json_str.encode("utf-8"))

        result = truncate_messages_by_size(messages, max_bytes=exact_size)
        assert len(result) == 1

        result = truncate_messages_by_size(messages, max_bytes=exact_size - 1)
        assert len(result) == 1


class TestSerializeGenAiMessages:
    def test_serialize_normal_messages(self, sample_messages):
        """Test serialization of normal messages"""
        result = serialize_gen_ai_messages(sample_messages)

        assert result is not None
        assert isinstance(result, str)

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) <= len(sample_messages)

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
        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 100
        result = serialize_gen_ai_messages(large_messages, max_bytes=small_limit)

        if result:
            assert isinstance(result, str)

            result_size = len(result.encode("utf-8"))
            assert result_size <= small_limit

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


class TestTruncateAndSerializeMessages:
    def test_main_function_with_normal_messages(self, sample_messages):
        """Test the main function with normal messages"""
        result = truncate_and_serialize_messages(sample_messages)
        assert isinstance(result, str)

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == len(sample_messages)

    def test_main_function_with_large_messages(self, large_messages):
        """Test the main function with messages requiring truncation"""
        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 100  # 5KB limit to force truncation
        result = truncate_and_serialize_messages(large_messages, max_bytes=small_limit)
        assert isinstance(result, AnnotatedValue)
        assert result.metadata["len"] == len(large_messages)
        assert isinstance(result.value, str)

        parsed = json.loads(result.value)
        assert isinstance(parsed, list)
        assert len(parsed) <= len(large_messages)

        result_size = len(result.value.encode("utf-8"))
        assert result_size <= small_limit

    def test_main_function_with_none_input(self):
        """Test the main function with None input"""
        result = truncate_and_serialize_messages(None)
        assert result is None

    def test_main_function_with_empty_input(self):
        """Test the main function with empty input"""
        result = truncate_and_serialize_messages([])
        assert result is None

    def test_main_function_serialization_format(self, sample_messages):
        """Test that the function always returns proper JSON strings"""
        result = truncate_and_serialize_messages(sample_messages)
        assert isinstance(result, str)

        parsed = json.loads(result)
        assert isinstance(parsed, list)

        for i, msg in enumerate(parsed):
            assert "role" in msg
            assert "content" in msg

    def test_main_function_default_limit(self, sample_messages):
        """Test that the main function uses the default limit correctly"""
        result = truncate_and_serialize_messages(sample_messages)
        assert isinstance(result, str)

        parsed = json.loads(result)
        assert isinstance(parsed, list)


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
        assert result is not None

        parsed = json.loads(result)
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
        assert result is not None

        if isinstance(result, AnnotatedValue):
            parsed = json.loads(result.value)
        else:
            parsed = json.loads(result)
        assert "metadata" in parsed[0]
        assert "tool_calls" in parsed[1]

    def test_messages_with_none_values(self):
        """Test messages containing None values"""
        messages = [
            {"role": "user", "content": None},
            {"role": "assistant", "content": "Hello", "extra": None},
        ]

        result = truncate_and_serialize_messages(messages)
        assert result is not None

        if isinstance(result, AnnotatedValue):
            parsed = json.loads(result.value)
        else:
            parsed = json.loads(result)
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

        small_limit = MAX_GEN_AI_MESSAGE_BYTES // 500
        result = truncate_and_serialize_messages(messages, max_bytes=small_limit)

        if result:
            assert isinstance(result, AnnotatedValue)
            parsed = json.loads(result.value)
            if parsed:
                last_kept_content = parsed[-1]["content"]
                assert (
                    "Message 9" in last_kept_content or "Message 8" in last_kept_content
                )


class TestMetaSupport:
    """Test that _meta entries are created correctly when truncation occurs"""

    def test_annotated_value_returned_on_truncation(self, large_messages):
        """Test that truncate_and_serialize_messages returns AnnotatedValue when truncation occurs"""
        small_limit = 50_000
        result = truncate_and_serialize_messages(large_messages, max_bytes=small_limit)
        assert isinstance(result, AnnotatedValue)
        assert result.metadata == {"len": len(large_messages)}
        assert isinstance(result.value, str)

        parsed = json.loads(result.value)
        assert len(parsed) <= len(large_messages)

    def test_no_annotated_value_when_no_truncation(self, sample_messages):
        """Test that truncate_and_serialize_messages returns plain list when no truncation occurs"""
        result = truncate_and_serialize_messages(sample_messages)
        assert not isinstance(result, AnnotatedValue)
        assert isinstance(result, str)

        parsed = json.loads(result)
        assert len(parsed) == len(sample_messages)

    def test_meta_structure_in_serialized_output(self, large_messages):
        """Test that _meta structure is created correctly in serialized output"""
        small_limit = 50_000
        annotated_messages = truncate_and_serialize_messages(
            large_messages, max_bytes=small_limit
        )
        test_data = {"gen_ai": {"request": {"messages": annotated_messages}}}
        serialized = serialize(test_data, is_vars=False)
        assert "_meta" in serialized
        assert "gen_ai" in serialized["_meta"]
        assert "request" in serialized["_meta"]["gen_ai"]
        assert "messages" in serialized["_meta"]["gen_ai"]["request"]
        assert serialized["_meta"]["gen_ai"]["request"]["messages"][""] == {
            "len": len(large_messages)
        }
        assert "gen_ai" in serialized
        assert "request" in serialized["gen_ai"]
        assert "messages" in serialized["gen_ai"]["request"]
        assert isinstance(serialized["gen_ai"]["request"]["messages"], str)

    def test_serialize_gen_ai_messages_handles_annotated_value(self, large_messages):
        """Test that serialize_gen_ai_messages handles AnnotatedValue input correctly"""
        truncated = large_messages[:2]
        annotated = AnnotatedValue(
            value=truncated, metadata={"len": len(large_messages)}
        )
        result = serialize_gen_ai_messages(annotated)

        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_empty_messages_no_annotated_value(self):
        """Test that empty messages don't create AnnotatedValue"""
        result = truncate_and_serialize_messages([])
        assert result is None

        result = truncate_and_serialize_messages(None)
        assert result is None

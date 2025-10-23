import json
import uuid

import pytest

import sentry_sdk
from sentry_sdk._types import AnnotatedValue
from sentry_sdk.ai.monitoring import ai_track
from sentry_sdk.ai.utils import (
    MAX_GEN_AI_MESSAGE_BYTES,
    set_data_normalized,
    truncate_and_annotate_messages,
    truncate_messages_by_size,
    _find_truncation_index,
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

        if result:
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

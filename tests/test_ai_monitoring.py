import pytest

import sentry_sdk
from sentry_sdk.ai.monitoring import ai_track
from sentry_sdk.ai.utils import (
    parse_data_uri,
)


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

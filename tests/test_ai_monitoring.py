import pytest

from sentry_sdk.ai.utils import (
    get_modality_from_mime_type,
    parse_data_uri,
    transform_anthropic_content_part,
    transform_content_part,
    transform_generic_content_part,
    transform_google_content_part,
    transform_openai_content_part,
)


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

import pytest
from sentry_sdk.tracing import Span


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("int", 123, "123"),
        ("float", 123.456, "123.456"),
        ("bool_true", True, "True"),
        ("bool_false", False, "False"),
        ("none", None, "None"),
        ("list", [1, 2, 3], "[1, 2, 3]"),
        ("dict", {"key": "value"}, "{'key': 'value'}"),
        ("already_string", "test", "test"),
    ],
)
def test_span_set_tag_converts_to_string(key, value, expected):
    """Test that Span.set_tag converts various types to strings."""
    span = Span()
    span.set_tag(key, value)

    # Check the tags in the span's JSON representation
    span_json = span.to_json()
    tags = span_json.get("tags", {})

    assert tags[key] == expected, f"Tag {key} was not converted properly"


def test_span_set_tag_handles_conversion_failure():
    """Test that Span.set_tag handles objects that fail to convert to string,
    but have a valid __repr__."""
    span = Span()

    # Create an object that raises an exception when str() is called
    class BadObject:
        def __str__(self):
            raise NotImplementedError("Cannot convert to string")

        def __repr__(self):
            return "BadObject()"

    bad_obj = BadObject()

    # This should not raise an exception
    span.set_tag("bad_object", bad_obj)

    # The tag should not be set
    span_json = span.to_json()
    tags = span_json.get("tags", {})

    assert tags["bad_object"] == "BadObject()"


def test_span_set_tag_handles_broken_repr():
    """Test that Span.set_tag handles objects with broken __str__ and __repr__."""
    span = Span()

    # Create an object that raises exceptions for both __str__ and __repr__
    class BadObject:
        def __str__(self):
            raise NotImplementedError("Cannot convert to string")

        def __repr__(self):
            raise NotImplementedError("Cannot get representation")

    bad_obj = BadObject()

    # This should not raise an exception
    span.set_tag("bad_object", bad_obj)

    # The tag should be set to a fallback value
    span_json = span.to_json()
    tags = span_json.get("tags", {})

    assert tags["bad_object"] == "<broken repr>"

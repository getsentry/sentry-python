import pytest
import sentry_sdk


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("int", 123, "123"),
        ("float", 123.456, "123.456"),
        ("bool_true", True, "True"),
        ("bool_false", False, "False"),
        ("list", [1, 2, 3], "(1, 2, 3)"),
        ("dict", {"key": "value"}, '{"key": "value"}'),
        ("already_string", "test", "test"),
    ],
)
def test_span_set_tag_converts_to_string(
    sentry_init, capture_events, key, value, expected
):
    """Test that Span.set_tag converts various types to strings."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_span() as span:
        span.set_tag(key, value)

    (event,) = events

    assert event["tags"][key] == expected, f"Tag {key} was not converted properly"


def test_span_set_tag_handles_conversion_failure(sentry_init, capture_events):
    """Test that Span.set_tag handles objects that fail to convert to string,
    but have a valid __repr__."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    # Create an object that raises an exception when str() is called.
    # This needs to be a subclass of something that otel supports as an
    # attribute value (e.g. int).
    class BadObject(int):
        def __str__(self):
            raise NotImplementedError("Cannot convert to string")

        def __repr__(self):
            return "BadObject()"

    with sentry_sdk.start_span() as span:
        span.set_tag("bad_object", BadObject())

    (event,) = events

    assert event["tags"]["bad_object"] == "BadObject()"


def test_span_set_tag_handles_broken_repr(sentry_init, capture_events):
    """Test that Span.set_tag handles objects with broken __str__ and __repr__."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    # Create an object that raises exceptions for both __str__ and __repr__
    class BadObject(int):
        def __str__(self):
            raise NotImplementedError("Cannot convert to string")

        def __repr__(self):
            raise NotImplementedError("Cannot get representation")

    bad_obj = BadObject()

    # This should not raise an exception
    with sentry_sdk.start_span() as span:
        span.set_tag("bad_object", bad_obj)

    # The tag should be set to a fallback value
    (event,) = events

    assert event["tags"]["bad_object"] == "<broken repr>"

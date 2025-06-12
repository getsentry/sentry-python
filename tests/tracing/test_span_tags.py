import pytest
import sentry_sdk
from sentry_sdk.tracing import Span, Transaction


def test_span_set_tag_converts_to_string():
    """Test that Span.set_tag converts various types to strings."""
    span = Span()
    
    # Test various types
    test_cases = [
        ("int", 123, "123"),
        ("float", 123.456, "123.456"),
        ("bool_true", True, "True"),
        ("bool_false", False, "False"),
        ("none", None, "None"),
        ("list", [1, 2, 3], "[1, 2, 3]"),
        ("dict", {"key": "value"}, "{'key': 'value'}"),
        ("already_string", "test", "test"),
    ]
    
    for key, value, expected in test_cases:
        span.set_tag(key, value)
    
    # Check the tags in the span's JSON representation
    span_json = span.to_json()
    tags = span_json.get("tags", {})
    
    for key, value, expected in test_cases:
        assert tags[key] == expected, f"Tag {key} was not converted properly"


def test_span_set_tag_handles_conversion_failure():
    """Test that Span.set_tag handles objects that fail to convert to string."""
    span = Span()
    
    # Create an object that raises an exception when str() is called
    class BadObject:
        def __str__(self):
            raise Exception("Cannot convert to string")
    
    bad_obj = BadObject()
    
    # This should not raise an exception
    span.set_tag("bad_object", bad_obj)
    
    # The tag should not be set
    span_json = span.to_json()
    tags = span_json.get("tags", {})
    
    assert "bad_object" not in tags, "Tag with failed conversion should not be set"
    
    # Other tags should still work
    span.set_tag("good_tag", "value")
    span_json = span.to_json()
    tags = span_json.get("tags", {})
    
    assert tags["good_tag"] == "value"


def test_transaction_set_tag_converts_to_string(sentry_init, capture_envelopes):
    """Test that Transaction.set_tag (inherited from Span) converts values to strings."""
    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()
    
    with sentry_sdk.start_transaction(name="test_transaction") as transaction:
        # Test various types
        transaction.set_tag("int", 123)
        transaction.set_tag("float", 123.456)
        transaction.set_tag("bool", True)
        transaction.set_tag("none", None)
        transaction.set_tag("list", [1, 2, 3])
        transaction.set_tag("string", "test")
    
    envelope = envelopes[0]
    transaction_event = envelope.items[0].get_transaction_event()
    tags = transaction_event.get("tags", {})
    
    assert tags["int"] == "123"
    assert tags["float"] == "123.456"
    assert tags["bool"] == "True"
    assert tags["none"] == "None"
    assert tags["list"] == "[1, 2, 3]"
    assert tags["string"] == "test"


def test_child_span_set_tag_converts_to_string(sentry_init, capture_envelopes):
    """Test that child spans also convert tag values to strings."""
    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()
    
    with sentry_sdk.start_transaction(name="test_transaction") as transaction:
        with sentry_sdk.start_span(op="test_span") as span:
            span.set_tag("int", 123)
            span.set_tag("bool", False)
            span.set_tag("dict", {"nested": "value"})
    
    envelope = envelopes[0]
    transaction_event = envelope.items[0].get_transaction_event()
    spans = transaction_event.get("spans", [])
    
    assert len(spans) == 1
    span_tags = spans[0].get("tags", {})
    
    assert span_tags["int"] == "123"
    assert span_tags["bool"] == "False"
    assert span_tags["dict"] == "{'nested': 'value'}"


def test_span_set_tag_with_custom_object():
    """Test that Span.set_tag works with custom objects that have __str__."""
    
    class CustomObject:
        def __init__(self, value):
            self.value = value
        
        def __str__(self):
            return f"CustomObject({self.value})"
    
    span = Span()
    custom_obj = CustomObject("test_value")
    
    span.set_tag("custom", custom_obj)
    
    span_json = span.to_json()
    tags = span_json.get("tags", {})
    
    assert tags["custom"] == "CustomObject(test_value)"
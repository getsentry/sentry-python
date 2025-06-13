"""
Test to verify that the span_map is an instance variable, not a class variable.
This ensures that multiple SentryLangchainCallback instances don't share state.
"""

import pytest
from uuid import uuid4

from sentry_sdk.integrations.langchain import SentryLangchainCallback


def test_span_map_is_instance_variable():
    """Test that each SentryLangchainCallback instance has its own span_map."""
    # Create two separate callback instances
    callback1 = SentryLangchainCallback(
        max_span_map_size=100,
        include_prompts=True
    )
    callback2 = SentryLangchainCallback(
        max_span_map_size=100,
        include_prompts=True
    )
    
    # Verify they have different span_map instances
    assert id(callback1.span_map) != id(callback2.span_map), \
        "span_map should be an instance variable, not shared between instances"
    
    # Add an entry to callback1's span_map
    test_run_id = uuid4()
    callback1.span_map[test_run_id] = "test_value"
    
    # Verify callback2's span_map is not affected
    assert test_run_id not in callback2.span_map, \
        "callback2's span_map should not contain callback1's entries"
    
    # Verify callback1 still has the entry
    assert test_run_id in callback1.span_map
    assert callback1.span_map[test_run_id] == "test_value"


def test_span_map_isolation_with_multiple_instances():
    """Test that multiple callback instances maintain isolated span_maps."""
    callbacks = []
    num_callbacks = 5
    
    # Create multiple callbacks
    for i in range(num_callbacks):
        callback = SentryLangchainCallback(
            max_span_map_size=100,
            include_prompts=True
        )
        callbacks.append(callback)
    
    # Verify all span_maps are different instances
    span_map_ids = [id(cb.span_map) for cb in callbacks]
    assert len(set(span_map_ids)) == num_callbacks, \
        "Each callback should have its own unique span_map instance"
    
    # Add different entries to each callback's span_map
    for i, callback in enumerate(callbacks):
        run_id = uuid4()
        callback.span_map[run_id] = f"callback_{i}_value"
    
    # Verify each callback only has its own entry
    for i, callback in enumerate(callbacks):
        assert len(callback.span_map) == 1, \
            f"Callback {i} should only have one entry in its span_map"
        
        # Check that the value matches what we set
        for run_id, value in callback.span_map.items():
            assert value == f"callback_{i}_value", \
                f"Callback {i} should have its own value"


def test_span_map_not_class_attribute():
    """Test that span_map is not accessible as a class attribute."""
    # This should raise AttributeError if span_map is properly an instance variable
    with pytest.raises(AttributeError):
        _ = SentryLangchainCallback.span_map
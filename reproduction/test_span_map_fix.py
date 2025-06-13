#!/usr/bin/env python
"""
Simple test to verify that the span_map fix works correctly.
Tests that span_map is now an instance variable, not a class variable.
"""

import os
import sys
from uuid import uuid4

# Add the parent directory to sys.path to use the local sentry_sdk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sentry_sdk.integrations.langchain import SentryLangchainCallback


def test_span_map_is_instance_variable():
    """Test that each SentryLangchainCallback instance has its own span_map."""
    print("Test 1: Checking if span_map is an instance variable...")
    
    # Create two separate callback instances
    callback1 = SentryLangchainCallback(
        max_span_map_size=100,
        include_prompts=True
    )
    callback2 = SentryLangchainCallback(
        max_span_map_size=100,
        include_prompts=True
    )
    
    # Check if they have different span_map instances
    if id(callback1.span_map) == id(callback2.span_map):
        print("❌ FAIL: Both callbacks share the same span_map (class variable bug!)")
        return False
    else:
        print("✅ PASS: Each callback has its own span_map instance")
    
    # Add an entry to callback1's span_map
    test_run_id = uuid4()
    callback1.span_map[test_run_id] = "test_value"
    
    # Verify callback2's span_map is not affected
    if test_run_id in callback2.span_map:
        print("❌ FAIL: callback2's span_map contains callback1's entries")
        return False
    else:
        print("✅ PASS: callback2's span_map is isolated from callback1")
    
    # Verify callback1 still has the entry
    if test_run_id not in callback1.span_map:
        print("❌ FAIL: callback1 lost its span_map entry")
        return False
    else:
        print("✅ PASS: callback1 maintains its own span_map entries")
    
    return True


def test_span_map_isolation_with_multiple_instances():
    """Test that multiple callback instances maintain isolated span_maps."""
    print("\nTest 2: Testing isolation with multiple instances...")
    
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
    unique_ids = len(set(span_map_ids))
    
    if unique_ids != num_callbacks:
        print(f"❌ FAIL: Only {unique_ids} unique span_maps for {num_callbacks} callbacks")
        return False
    else:
        print(f"✅ PASS: All {num_callbacks} callbacks have unique span_maps")
    
    # Add different entries to each callback's span_map
    for i, callback in enumerate(callbacks):
        run_id = uuid4()
        callback.span_map[run_id] = f"callback_{i}_value"
    
    # Verify each callback only has its own entry
    for i, callback in enumerate(callbacks):
        if len(callback.span_map) != 1:
            print(f"❌ FAIL: Callback {i} has {len(callback.span_map)} entries instead of 1")
            return False
        
        # Check that the value matches what we set
        for run_id, value in callback.span_map.items():
            if value != f"callback_{i}_value":
                print(f"❌ FAIL: Callback {i} has wrong value: {value}")
                return False
    
    print("✅ PASS: Each callback maintains its own isolated span_map")
    return True


def test_span_map_not_class_attribute():
    """Test that span_map is not accessible as a class attribute."""
    print("\nTest 3: Checking that span_map is not a class attribute...")
    
    try:
        _ = SentryLangchainCallback.span_map
        print("❌ FAIL: span_map is still accessible as a class attribute")
        return False
    except AttributeError:
        print("✅ PASS: span_map is not accessible as a class attribute")
        return True


def main():
    print("="*60)
    print("Testing span_map Fix for Langchain Integration")
    print("="*60)
    
    all_passed = True
    
    # Run all tests
    all_passed &= test_span_map_is_instance_variable()
    all_passed &= test_span_map_isolation_with_multiple_instances()
    all_passed &= test_span_map_not_class_attribute()
    
    print("\n" + "="*60)
    if all_passed:
        print("✅ ALL TESTS PASSED! The span_map fix is working correctly.")
    else:
        print("❌ SOME TESTS FAILED! The span_map issue may not be fully fixed.")
    print("="*60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
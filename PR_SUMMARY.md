# PR Summary: Ensure tag values are strings

## Problem
Tag values must be strings according to the Sentry spec, but the Python SDK's `set_tag` methods accept `Any` type and don't convert values to strings. This causes issues when serializing envelopes for other services like Sentry CLI, which expect tag values to be strings.

## Solution
Modified all `set_tag` and `set_tags` methods to convert values to strings before storing them. If conversion fails, the tag is not set and a debug message is logged.

## Changes Made

### 1. `sentry_sdk/scope.py`
- **`Scope.set_tag()`**: Now converts values to strings using `str()`. If conversion fails, logs a debug message and doesn't set the tag.
- **`Scope.set_tags()`**: Now uses `self.set_tag()` internally to ensure all values are converted to strings.

### 2. `sentry_sdk/tracing.py`
- **`Span.set_tag()`**: Now converts values to strings using `str()`. If conversion fails, logs a debug message and doesn't set the tag.
- **`NoOpSpan.set_tag()`**: No changes needed (already a no-op).

### 3. `sentry_sdk/api.py`
- **`set_tag()` and `set_tags()`**: No changes needed (they delegate to `Scope` methods).

## Tests Added

### 1. `tests/test_scope.py`
- `test_set_tag_converts_to_string()`: Tests that various types (int, float, bool, None, list, dict) are converted to strings.
- `test_set_tags_converts_to_string()`: Tests that `set_tags` converts all values to strings.
- `test_set_tag_handles_conversion_failure()`: Tests that objects that fail to convert don't crash the SDK.
- `test_set_tags_handles_conversion_failure()`: Tests that `set_tags` handles conversion failures gracefully.

### 2. `tests/tracing/test_span_tags.py` (new file)
- `test_span_set_tag_converts_to_string()`: Tests Span tag conversion.
- `test_span_set_tag_handles_conversion_failure()`: Tests Span conversion failure handling.
- `test_transaction_set_tag_converts_to_string()`: Tests Transaction tag conversion.
- `test_child_span_set_tag_converts_to_string()`: Tests child span tag conversion.
- `test_span_set_tag_with_custom_object()`: Tests custom objects with `__str__` method.

### 3. `tests/test_api.py`
- `test_set_tag_converts_to_string()`: Tests the API `set_tag` function.
- `test_set_tags_converts_to_string()`: Tests the API `set_tags` function.

## Behavior Changes
1. All tag values are now converted to strings before being stored.
2. If a value cannot be converted to string (e.g., `__str__` raises an exception), the tag is silently ignored and a debug message is logged.
3. `set_tags()` now uses `set_tag()` internally instead of directly updating the tags dictionary.

## Backward Compatibility
The changes are backward compatible:
- Existing code will continue to work as before.
- String values are not affected.
- Non-string values that were previously accepted are now converted to strings.
- The only breaking case would be if someone was relying on non-string tag values, which violates the Sentry spec.

## Testing
All new tests pass and verify:
1. Various Python types are correctly converted to strings.
2. Conversion failures are handled gracefully without crashing.
3. The changes work correctly for both Scope and Span classes.
4. The API functions continue to work correctly.
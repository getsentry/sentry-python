# Sentry SDK 2.0 Migration Guide

**WIP:** Please add any 2.0 changes here with instructions how to adapt to the new behavior, if applicable.

## New Features

## Changed

- `start_child_span_decorator` was moved from `sentry_sdk.tracing_utils_py3` to `sentry_sdk.tracing_utils`.

## Removed

- Removed support for Python 2 and Python 3.5. The SDK now requires at least Python 3.6.
- `sentry_sdk.tracing_utils_py2` was removed.
- `sentry_sdk.tracing_utils_py3` was removed.
- `get_current_span` is no longer available from `sentry_sdk.tracing_utils`. Use `sentry_sdk.get_current_span` directly.

## Deprecated

# Sentry SDK 2.0 Migration Guide

**WIP:** Please add any 2.0 changes here with instructions how to adapt to the new behavior, if applicable.

## New Features

## Changed

- Moved the contents of `tracing_utils_py3.py` to `tracing_utils.py`. The `start_child_span_decorator` is now in `tracing_utils.py`.

## Removed

- Removed support for Python 2 and Python 3.5. The SDK now requires at least Python 3.6.
- Removed `tracing_utils_py2.py`. The `start_child_span_decorator` is now in `tracing_utils.py`.

## Deprecated

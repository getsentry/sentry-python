# Sentry SDK 2.0 Migration Guide

**WIP:** Please add any 2.0 changes here with instructions how to adapt to the new behavior, if applicable.

## New Features

## Changed

- The `BackgroundWorker` thread used to process events was renamed from `raven-sentry.BackgroundWorker` to `sentry-sdk.BackgroundWorker`.
- The `reraise` function was moved from `sentry_sdk._compat` to `sentry_sdk.utils`.
- Moved the contents of `tracing_utils_py3.py` to `tracing_utils.py`. The `start_child_span_decorator` is now in `tracing_utils.py`.
- The actual implementation of `get_current_span` was moved to `sentry_sdk.tracing_utils`. `sentry_sdk.get_current_span` is still accessible as part of the top-level API.

## Removed

- Removed support for Python 2 and Python 3.5. The SDK now requires at least Python 3.6.
- Removed support for Celery 3.\*.
- Removed support for Django 1.8, 1.9, 1.10.
- Removed support for Flask 0.\*.
- `sentry_sdk._functools` was removed.
- A number of compatibility utilities were removed from `sentry_sdk._compat`: the constants `PY2` and `PY33`; the functions `datetime_utcnow`, `utc_from_timestamp`, `implements_str`, `contextmanager`; and the aliases `text_type`, `string_types`, `number_types`, `int_types`, `iteritems`, `binary_sequence_types`.
- Removed `tracing_utils_py2.py`. The `start_child_span_decorator` is now in `tracing_utils.py`.

## Deprecated

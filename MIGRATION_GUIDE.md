# Sentry SDK 2.0 Migration Guide

Looking to upgrade from Sentry SDK 1.x to 2.x? Here's a comprehensive list of what's changed.

## New Features

- Additional integrations will now be activated automatically if the SDK detects the respective package is installed: Ariadne, ARQ, asyncpg, Chalice, clickhouse-driver, GQL, Graphene, huey, Loguru, PyMongo, Quart, Starlite, Strawberry.
- Added new API for custom instrumentation: `new_scope`, `isolation_scope`. See the [Deprecated](#deprecated) section to see how they map to the existing APIs.

## Changed

- The Pyramid integration will not capture errors that might happen in `authenticated_userid()` in a custom `AuthenticationPolicy` class.
- The method `need_code_loation` of the `MetricsAggregator` was renamed to `need_code_location`.
- The `BackgroundWorker` thread used to process events was renamed from `raven-sentry.BackgroundWorker` to `sentry-sdk.BackgroundWorker`.
- The `reraise` function was moved from `sentry_sdk._compat` to `sentry_sdk.utils`.
- The `_ScopeManager` was moved from `sentry_sdk.hub` to `sentry_sdk.scope`.
- The signature for the metrics callback function set with `before_emit_metric` has changed from `before_emit_metric(key, tags)` to `before_emit_metric(key, value, unit, tags)`
- Moved the contents of `tracing_utils_py3.py` to `tracing_utils.py`. The `start_child_span_decorator` is now in `sentry_sdk.tracing_utils`.
- The actual implementation of `get_current_span` was moved to `sentry_sdk.tracing_utils`. `sentry_sdk.get_current_span` is still accessible as part of the top-level API.
- `sentry_sdk.tracing_utils.add_query_source()`: Removed the `hub` parameter. It is not necessary anymore.
- `sentry_sdk.tracing_utils.record_sql_queries()`: Removed the `hub` parameter. It is not necessary anymore.
- `sentry_sdk.tracing_utils.get_current_span()` does now take a `scope` instead of a `hub` as parameter.
- `sentry_sdk.tracing_utils.should_propagate_trace()` now takes a `Client` instead of a `Hub` as first parameter.
- `sentry_sdk.utils.is_sentry_url()` now takes a `Client` instead of a `Hub` as first parameter.
- `sentry_sdk.utils._get_contextvars` does not return a tuple with three values, but a tuple with two values. The `copy_context` was removed.
- If you create a transaction manually and later mutate the transaction in a `configure_scope` block this does not work anymore. Here is a recipe on how to change your code to make it work:
    Your existing implementation:
    ```python
    transaction = sentry_sdk.transaction(...)

    # later in the code execution:

    with sentry_sdk.configure_scope() as scope:
        scope.set_transaction_name("new-transaction-name")
    ```

    needs to be changed to this:
    ```python
    transaction = sentry_sdk.transaction(...)

    # later in the code execution:

    scope = sentry_sdk.Scope.get_current_scope()
    scope.set_transaction_name("new-transaction-name")
    ```
- The classes listed in the table below are now abstract base classes. Therefore, they can no longer be instantiated. Subclasses can only be instantiated if they implement all of the abstract methods.
  <details>
    <summary><b>Show table</b></summary>

  | Class                                 | Abstract methods                       |
  | ------------------------------------- | -------------------------------------- |
  | `sentry_sdk.integrations.Integration` | `setup_once`                           |
  | `sentry_sdk.metrics.Metric`           | `add`, `serialize_value`, and `weight` |
  | `sentry_sdk.profiler.Scheduler`       | `setup` and `teardown`                 |
  | `sentry_sdk.transport.Transport`      | `capture_envelope`                     |

    </details>

## Removed

- Removed support for Python 2 and Python 3.5. The SDK now requires at least Python 3.6.
- Removed support for Celery 3.\*.
- Removed support for Django 1.8, 1.9, 1.10.
- Removed support for Flask 0.\*.
- Removed support for gRPC < 1.39.
- Removed support for Tornado < 6.
- Removed `last_event_id()` top level API. The last event ID is still returned by `capture_event()`, `capture_exception()` and `capture_message()` but the top level API `sentry_sdk.last_event_id()` has been removed.
- Removed support for sending events to the `/store` endpoint. Everything is now sent to the `/envelope` endpoint. If you're on SaaS you don't have to worry about this, but if you're running Sentry yourself you'll need version `20.6.0` or higher of self-hosted Sentry.
- The deprecated `with_locals` configuration option was removed. Use `include_local_variables` instead. See https://docs.sentry.io/platforms/python/configuration/options/#include-local-variables.
- The deprecated `request_bodies` configuration option was removed. Use `max_request_body_size`. See https://docs.sentry.io/platforms/python/configuration/options/#max-request-body-size.
- Removed support for `user.segment`. It was also removed from the trace header as well as from the dynamic sampling context.
- Removed support for the `install` method for custom integrations. Please use `setup_once` instead.
- Removed `sentry_sdk.tracing.Span.new_span`. Use `sentry_sdk.tracing.Span.start_child` instead.
- Removed `sentry_sdk.tracing.Transaction.new_span`. Use `sentry_sdk.tracing.Transaction.start_child` instead.
- Removed support for creating transactions via `sentry_sdk.tracing.Span(transaction=...)`. To create a transaction, please use `sentry_sdk.tracing.Transaction(name=...)`.
- Removed `sentry_sdk.utils.Auth.store_api_url`.
- `sentry_sdk.utils.Auth.get_api_url`'s now accepts a `sentry_sdk.consts.EndpointType` enum instead of a string as its only parameter. We recommend omitting this argument when calling the function, since the parameter's default value is the only possible `sentry_sdk.consts.EndpointType` value. The parameter exists for future compatibility.
- Removed `tracing_utils_py2.py`. The `start_child_span_decorator` is now in `sentry_sdk.tracing_utils`.
- Removed the `sentry_sdk.profiler.Scheduler.stop_profiling` method. Any calls to this method can simply be removed, since this was a no-op method.
- Removed the experimental `metrics_summary_sample_rate` config option.
- Removed the experimental `should_summarize_metric` config option.

## Deprecated

- Using the `Hub` directly as well as using hub-based APIs has been deprecated. Where available, use [the top-level API instead](sentry_sdk/api.py); otherwise use the [scope API](sentry_sdk/scope.py) or the [client API](sentry_sdk/client.py).

  Before:

  ```python
  with hub.start_span(...):
      # do something
  ```

  After:

  ```python
  import sentry_sdk

  with sentry_sdk.start_span(...):
      # do something
  ```

- Hub cloning is deprecated.

  Before:

  ```python
  with Hub(Hub.current) as hub:
      # do something with the cloned hub
  ```

  After:

  ```python
  import sentry_sdk

  with sentry_sdk.isolation_scope() as scope:
      # do something with the forked scope
  ```

- `configure_scope` is deprecated. Use the new isolation scope directly via `Scope.get_isolation_scope()` instead.

  Before:

  ```python
  with configure_scope() as scope:
      # do something with `scope`
  ```

  After:

  ```python
  from sentry_sdk.scope import Scope

  scope = Scope.get_isolation_scope()
  # do something with `scope`
  ```

- `push_scope` is deprecated. Use the new `new_scope` context manager to fork the necessary scopes.

  Before:

  ```python
  with push_scope() as scope:
      # do something with `scope`
  ```

  After:

  ```python
  import sentry_sdk

  with sentry_sdk.new_scope() as scope:
      # do something with `scope`
  ```

- Accessing the client via the hub has been deprecated. Use the top-level `sentry_sdk.get_client()` to get the current client.
- `profiler_mode` and `profiles_sample_rate` have been deprecated as `_experiments` options. Use them as top level options instead:
  ```python
  sentry_sdk.init(
      ...,
      profiler_mode="thread",
      profiles_sample_rate=1.0,
  )
  ```
- Deprecated `sentry_sdk.transport.Transport.capture_event`. Please use `sentry_sdk.transport.Transport.capture_envelope`, instead.
- Passing a function to `sentry_sdk.init`'s `transport` keyword argument has been deprecated. If you wish to provide a custom transport, please pass a `sentry_sdk.transport.Transport` instance or a subclass.
- The parameter `propagate_hub` in `ThreadingIntegration()` was deprecated and renamed to `propagate_scope`.

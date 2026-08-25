# Sentry SDK 3.0 Migration Guide


Looking to upgrade from Sentry SDK 2.x to 3.x? Here's a comprehensive list of what's changed. Looking for a more digestable summary? See the [guide in the docs](https://docs.sentry.io/platforms/python/migration/2.x-to-3.x) with the most common migration patterns.

## New Features


## Changed

- The Strawberry integration won't auto-enable anymore if we detect `strawberry-graphql` is installed. Set it up manually, setting the `async_execution` integration option to either `True` or `False` depending on if your app is async or sync.

  ```python
  from sentry_sdk.integrations.strawberry import StrawberryIntegration

  sentry_sdk.init(
      integrations=[
          StrawberryIntegration(async_execution=True),  # or False
      ],
      ...
  )
  ```

- The UnraisableHookIntegration is now enabled by default.
- We now don't suppress chained exceptions in the ASGI and asyncio integrations by default. The related `suppress_asgi_chained_exceptions` experimental option was removed.

### Logging

- The standard library logging integration is not auto-enabled by default anymore. To continue using it, add it to the `integrations` list in your `sentry_sdk.init()`:

  ```python
  import sentry_sdk
  from sentry_sdk.integrations.logging import LoggingIntegration

  sentry_sdk.init(
      integrations=[
          LoggingIntegration(),
      ]
  )
  ```

- The `level` integration option is now called `breadcrumb_level`.
- The `sentry_logs_level` integration option is now called `level`.
- The `capture_sentry_logs` option was removed.
- The `ignore_logger` helper was renamed to `ignore_logger_for_breadcrumbs_and_events`.
- The `ignore_logger_for_sentry_logs` helper was renamed to `ignore_logger`.
- `SentryHandler` was removed. Use `EventHandler` instead.
- When you enable the integration by adding `LoggingIntegration` to your `sentry_sdk.init()`, it'll start capturing Sentry logs. The other features (breadcrumbs, events from logs) can be enabled by providing additional integration options.

  | Old name | New name | Old default | New default | Description |
  | --- | --- | --- |
  | `level` | `breadcrumb_level` | `INFO` | off | Captures logs of that level and higher as breadcrumbs. |
  | `event_level` | `event_level` | `INFO` | off | Captures logs of that level and higher as events. |
  | `sentry_logs_level` | `level` | `INFO` | `INFO` | Captures logs of that level and higher as Sentry logs. |
  | `capture_sentry_logs` | removed | `False` | on | Allows to opt out of instrumenting logs as Sentry logs. Use `level` (previously `sentry_logs_level`) to adjust what should be captured instead. |
  | `ignore_logger` | `ignore_logger_for_events` | Loggers that match this name will not create breadcrumbs and events. |
  | `unignore_logger` | `unignore_logger_for_events` | Loggers that match this name will create breadcrumbs and events again. |
  | `ignore_logger_for_sentry_logs` | `ignore_logger` | Loggers that match this name will not create Sentry logs. |
  | `unignore_logger_for_sentry_logs` | `unignore_logger` | Loggers that match this name will create Sentry logs again. |


## Removed

- The SDK no longer supports Python 3.6. The oldest supported version is now 3.7.
- Dropped support for Django versions below 2.0.
- Dropped support for gevent versions below 20.9.
- Dropped support for greenlet versions below 0.4.17.
- Dropped support for Falcon versions below 3.0.
- Dropped support for Flask below 2.0.
- Dropped support for Chalice below 1.22.
- Dropped support for aiohttp below 3.7.
- Dropped support for Starlette below 0.20.
- Dropped support for FastAPI below 0.85.
- Dropped support for trytond below 5.4.
- Dropped support for Pyramid below 2.0.
- Dropped support for rq below 1.0.
- Dropped support for Quart below 0.19.
- Dropped support for Sanic below 22.0.
- Dropped support for redis-py below 4.2.
- Removed the RedisIntegration `max_data_size` option.
- Removed the possibility to supply a specific client to the LaunchDarklyIntegration.
- The `enable_tracing` option was removed. Use `traces_sample_rate=1.0` instead.
- The `enable_logs` option was removed. Using Sentry's logging API now works without requiring setting `enable_logs=True`. Automatic capture of logs emitted by the `logging` standard library module or Loguru can be turned on by providing the `capture_sentry_logs=True` option to either `LoggingIntegration` or `LoguruIntegration`:

  ```python
  import sentry_sdk
  from sentry_sdk.integrations.logging import LoggingIntegration
  from sentry_sdk.integrations.loguru import LoguruIntegration

  sentry_sdk.init(
      integrations=[
          LoggingIntegration(capture_sentry_logs=True),
          LoguruIntegration(capture_sentry_logs=True),
      ]
  )
  ```

- The `enable_metrics` option was removed.
- The deprecated `@ai_track` decorator was removed.
- The deprecated `push_scope` and `configure_scope` APIs have been removed. Use `with new_scope():` to push a new scope and `scope = get_current_scope()` to retrieve the current scope instead.
- Transaction profiling and related code was removed.
- The `start_profile_session` and `stop_profile_session` were removed in favor of `start_profile` and `stop_profile`, respectively.
- The experimental `continuous_profiling_mode` option was removed. Use the top-level `profiler_mode`, instead.
- Removed the deprecated Hub class and all uses of hub throughout the SDK in arguments, options, etc. Use a scope instead.
- The `SentrySpanProcessor`, `SentryPropagator`, `instrumenter`, and associated OpenTelemetry compatibility code was removed along with the `opentelemetry` extra and the `SentryPropagator` entrypoint. Use the `OTLPIntegration` instead.
- Removed the `auto_session_tracing` decorator. Use `track_session` instead.
- The deprecated `set_measurement` API was removed.
- The experimental option `otel_powered_performance` has been removed together with the associated `OpenTelemetryIntegration` and `opentelemetry-experimental` extra.
- A number of extras (installable via `sentry-sdk[extra-name]`) has been removed. Use the base package (`sentry-sdk`) instead; there is no difference in functionality. The following extras have been removed: `aiohttp`, `anthropic`, `arq`, `asyncpg`, `beam`, `bottle`, `celery`, `celery-redbeat`, `chalice`, `clickhouse-driver`, `django`, `falcon`, `fastapi`, `google-genai`, `httpx`, `huey`, `huggingface_hub`, `langchain`, `langgraph`, `launchdarkly`, `litellm`, `litestar`, `loguru`, `mcp`, `openai`, `openfeature`, `pydantic_ai`, `pymongo`, `pyspark`, `rq`, `sanic`, `sqlalchemy`, `starlette`, `starlite`, `statsig`, `tornado`, `unleash`.
- The `failed_request_status_codes` integration option now only supports a set of integers as input. Lists of integers or containers of integers are no longer supported.
- The deprecated `propagate_traces` option has been removed. Use `trace_propagation_targets` instead, which gives you more power over trace propagation. Note that only the top-level `init` option was removed; the `propagate_traces` option of the Celery integration remains available.
- Removed Spotlight integration for Django. See [Spotlight 2.0](https://github.com/getsentry/spotlight/issues/891) for more context.

## Deprecated


# Sentry SDK 2.0 Migration Guide

Looking to upgrade from Sentry SDK 1.x to 2.x? Here's a comprehensive list of what's changed. Looking for a more digestable summary? See the [guide in the docs](https://docs.sentry.io/platforms/python/migration/1.x-to-2.x) with the most common migration patterns.

## New Features

- Additional integrations will now be activated automatically if the SDK detects the respective package is installed: Ariadne, ARQ, asyncpg, Chalice, clickhouse-driver, GQL, Graphene, huey, Loguru, PyMongo, Quart, Starlite, Strawberry.
- While refactoring the [inner workings](https://docs.sentry.io/platforms/python/enriching-events/scopes/) of the SDK we added new top-level APIs for custom instrumentation called `new_scope` and `isolation_scope`. See the [Deprecated](#deprecated) section to see how they map to the existing APIs.

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
- You no longer have to use `configure_scope` to mutate a transaction. Instead, you simply get the current scope to mutate the transaction. Here is a recipe on how to change your code to make it work:
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

  scope = sentry_sdk.get_current_scope()
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

- `configure_scope` is deprecated. Modify the current or isolation scope directly instead.

  Before:

  ```python
  with configure_scope() as scope:
      # do something with `scope`
  ```

  After:

  ```python
  from sentry_sdk import get_current_scope

  scope = get_current_scope()
  # do something with `scope`
  ```

  Or:

  ```python
  from sentry_sdk import get_isolation_scope

  scope = get_isolation_scope()
  # do something with `scope`
  ```

  When to use `get_current_scope()` and `get_isolation_scope()` depends on how long the change to the scope should be in effect. If you want the changed scope to affect the whole request-response cycle or the whole execution of task, use the isolation scope. If it's more localized, use the current scope.

- `push_scope` is deprecated. Fork the current or the isolation scope instead.

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

  Or:

  ```python
  import sentry_sdk

  with sentry_sdk.isolation_scope() as scope:
      # do something with `scope`
  ```

  `new_scope()` will fork the current scope, while `isolation_scope()` will fork the isolation scope. The lifecycle of a single isolation scope roughly translates to the lifecycle of a transaction in most cases, so if you're looking to create a new separated scope for a whole request-response cycle or task execution, go for `isolation_scope()`. If you want to wrap a smaller unit code, fork the current scope instead with `new_scope()`.

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

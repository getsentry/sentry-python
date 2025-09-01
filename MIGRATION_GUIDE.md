# Sentry SDK Migration Guide

## Upgrading to 3.0

Looking to upgrade from Sentry SDK 2.x to 3.x? Here's a comprehensive list of what's changed. Looking for a more digestible summary? See the [guide in the docs](https://docs.sentry.io/platforms/python/migration/2.x-to-3.x) with the most common migration patterns.

### New Features

- Added `add_attachment()` as a top level API, so you can do now: `sentry_sdk.add_attachment(...)` (up until now it was only available on the `Scope`)
- Added a new SDK option `exclude_span_origins`. Spans with an `origin` from `exclude_span_origins` won't be created. This can be used for example in dual OpenTelemetry/Sentry setups to filter out spans from specific Sentry instrumentations. Note that using `exclude_span_origins` might potentially lead to surprising results: if, for example, a root span is excluded based on `origin`, all of its children will become root spans, unless they were started with `only_as_child_span=True`.
- Added a new experimental async transport using the [httpcore](https://pypi.org/project/httpcore/) library. Requires the [httpcore[asyncio]](https://www.encode.io/httpcore/async/) extension. The async transport makes Sentry network I/O non-blocking in async programs. The async transport can only be used when an event loop is running, and additionally requires the [asyncio](https://docs.sentry.io/platforms/python/integrations/asyncio/) integration. Can be enabled by passing ``transport_async=true`` in the experiments dict in ``sentry.init()``.
- Added an asynchronous context manager for spans. This can be used to remove unnessecary nesting for span creation in async programs.
- Added `flush_async()` as a top level API. This has to be used when flushing the async transport. `flush_async` is a coroutine and can be awaited if blocking behaviour is desired.
### Changed

#### General

- The SDK now supports Python 3.7 and higher.
- Tag values on event dictionaries, which are passed to `before_send` and `before_send_transaction`, now are always `str` values. Previously, despite tag values being typed as `str`, they often had different values. Therefore, if you have configured any `before_send` and `before_send_transaction` functions which perform some logic based on tag values, you need to check and if needed update those functions to correctly handle `str` values.

#### Error Capturing

- We updated how we handle `ExceptionGroup`s. You will now get more data if `ExceptionGroup`s are appearing in chained exceptions. It could happen that after updating the SDK the grouping of issues change because of this. So eventually you will see the same exception in two Sentry issues (one from before the update, one from after the update).

#### Tracing

- The default of `traces_sample_rate` changed to `0`. Meaning: Incoming traces will be continued by default. For example, if your frontend sends a `sentry-trace/baggage` headers pair, your SDK will create Spans and send them to Sentry. (The default used to be `None` meaning by default no Spans where created, no matter what headers the frontend sent to your project.) See also: https://docs.sentry.io/platforms/python/configuration/options/#traces_sample_rate
- `sentry_sdk.start_span` now only takes keyword arguments.
- `sentry_sdk.start_transaction`/`sentry_sdk.start_span` no longer takes the following arguments: `span`, `parent_sampled`, `trace_id`, `span_id` or `parent_span_id`.
- `sentry_sdk.continue_trace` no longer returns a `Transaction` and is now a context manager. 

    - Use it to continue an upstream trace with the `sentry-trace` and `baggage` headers.

    ```python
    headers = {"sentry-trace": "{trace_id}-{span_id}-{sampled_flag}", "baggage": "{baggage header}"}
    with sentry_sdk.continue_trace(headers):
        with sentry_sdk.start_span(name="continued span in trace"):
            pass
    ```

    - If the headers are empty, a new trace will be started.
    - If you want to force creation of a new trace, use the `sentry_sdk.new_trace` context manager.

- You can no longer change the sampled status of a span with `span.sampled = False` after starting it. The sampling decision needs to be either be made in the `traces_sampler`, or you need to pass an explicit `sampled` parameter to `start_span`.
- `sentry_sdk.start_span` now takes an optional `only_as_child_span` argument. These spans will not be started if they would be root spans -- they can only exist as child spans. You can use this parameter to prevent spans without a parent from becoming root spans.
- The `Span()` constructor does not accept a `hub` parameter anymore.
- `Span.finish()` does not accept a `hub` parameter anymore.
- `Span.finish()` no longer returns the `event_id` if the event is sent to Sentry.
- The `sampling_context` argument of `traces_sampler` now additionally contains all span attributes known at span start.
- The `SentrySpanProcessor` and `SentryPropagator` are exported from `sentry_sdk.opentelemetry` instead of `sentry_sdk.integrations.opentelemetry`.

#### Profiling

- The `sampling_context` argument of `profiles_sampler` now additionally contains all span attributes known at span start.
- The `Profile()` constructor does not accept a `hub` parameter anymore.
- A `Profile` object does not have a `.hub` property anymore.
- `MAX_PROFILE_DURATION_NS`, `PROFILE_MINIMUM_SAMPLES`, `Profile`, `Scheduler`, `ThreadScheduler`, `GeventScheduler`, `has_profiling_enabled`, `setup_profiler`, `teardown_profiler` are no longer accessible from `sentry_sdk.profiler`. They're still accessible from `sentry_sdk.profiler.transaction_profiler`.
- `DEFAULT_SAMPLING_FREQUENCY`, `MAX_STACK_DEPTH`, `get_frame_name`, `extract_frame`, `extract_stack`, `frame_id` are no longer accessible from `sentry_sdk.profiler`. They're still accessible from `sentry_sdk.profiler.utils`.

#### Logs

- `enable_logs` and `before_send_log` are now regular SDK options. Their original versions under `_experiments` have been removed.

#### Integrations

- AWS Lambda, GCP: The message of the warning the SDK optionally emits if a function is about to time out has changed.
- Redis: In Redis pipeline spans there is no `span["data"]["redis.commands"]` that contains a dict `{"count": 3, "first_ten": ["cmd1", "cmd2", ...]}` but instead `span["data"]["redis.commands.count"]` (containing `3`) and `span["data"]["redis.commands.first_ten"]` (containing `["cmd1", "cmd2", ...]`).
- clickhouse-driver: The query is now available under the `db.query.text` span attribute (only if `send_default_pii` is `True`).
- Logging: By default, the SDK won't capture Sentry issues anymore when calling `logging.error()`, `logging.critical()` or `logging.exception()`. If you want to preserve the old behavior use `sentry_sdk.init(integrations=[LoggingIntegration(event_level="ERROR")])`.
- The integration-specific content of the `sampling_context` argument of `traces_sampler` and `profiles_sampler` now looks different.

  - The Celery integration doesn't add the `celery_job` dictionary anymore. Instead, the individual keys are now available as:

    | Dictionary keys        | Sampling context key        | Example                        |
    | ---------------------- | --------------------------- | ------------------------------ |
    | `celery_job["args"]`   | `celery.job.args.{index}`   | `celery.job.args.0`            |
    | `celery_job["kwargs"]` | `celery.job.kwargs.{kwarg}` | `celery.job.kwargs.kwarg_name` |
    | `celery_job["task"]`   | `celery.job.task`           |                                |

    Note that all of these are serialized, i.e., not the original `args` and `kwargs` but rather OpenTelemetry-friendly span attributes.

  - The AIOHTTP integration doesn't add the `aiohttp_request` object anymore. Instead, some of the individual properties of the request are accessible, if available, as follows:

    | Request property  | Sampling context key(s)         |
    | ----------------- | ------------------------------- |
    | `path`            | `url.path`                      |
    | `query_string`    | `url.query`                     |
    | `method`          | `http.request.method`           |
    | `host`            | `server.address`, `server.port` |
    | `scheme`          | `url.scheme`                    |
    | full URL          | `url.full`                      |
    | `request.headers` | `http.request.header.{header}`  |

  - The Tornado integration doesn't add the `tornado_request` object anymore. Instead, some of the individual properties of the request are accessible, if available, as follows:

    | Request property  | Sampling context key(s)                             |
    | ----------------- | --------------------------------------------------- |
    | `path`            | `url.path`                                          |
    | `query`           | `url.query`                                         |
    | `protocol`        | `url.scheme`                                        |
    | `method`          | `http.request.method`                               |
    | `host`            | `server.address`, `server.port`                     |
    | `version`         | `network.protocol.name`, `network.protocol.version` |
    | full URL          | `url.full`                                          |
    | `request.headers` | `http.request.header.{header}`                      |

  - The WSGI integration doesn't add the `wsgi_environ` object anymore. Instead, the individual properties of the environment are accessible, if available, as follows:

    | Env property      | Sampling context key(s)                           |
    | ----------------- | ------------------------------------------------- |
    | `PATH_INFO`       | `url.path`                                        |
    | `QUERY_STRING`    | `url.query`                                       |
    | `REQUEST_METHOD`  | `http.request.method`                             |
    | `SERVER_NAME`     | `server.address`                                  |
    | `SERVER_PORT`     | `server.port`                                     |
    | `SERVER_PROTOCOL` | `server.protocol.name`, `server.protocol.version` |
    | `wsgi.url_scheme` | `url.scheme`                                      |
    | full URL          | `url.full`                                        |
    | `HTTP_*`          | `http.request.header.{header}`                    |

  - The ASGI integration doesn't add the `asgi_scope` object anymore. Instead, the individual properties of the scope, if available, are accessible as follows:

    | Scope property | Sampling context key(s)         |
    | -------------- | ------------------------------- |
    | `type`         | `network.protocol.name`         |
    | `scheme`       | `url.scheme`                    |
    | `path`         | `url.path`                      |
    | `query`        | `url.query`                     |
    | `http_version` | `network.protocol.version`      |
    | `method`       | `http.request.method`           |
    | `server`       | `server.address`, `server.port` |
    | `client`       | `client.address`, `client.port` |
    | full URL       | `url.full`                      |
    | `headers`      | `http.request.header.{header}`  |

  - The RQ integration doesn't add the `rq_job` object anymore. Instead, the individual properties of the job and the queue, if available, are accessible as follows:

    | RQ property     | Sampling context key         | Example                |
    | --------------- | ---------------------------- | ---------------------- |
    | `rq_job.args`   | `rq.job.args.{index}`        | `rq.job.args.0`        |
    | `rq_job.kwargs` | `rq.job.kwargs.{kwarg}`      | `rq.job.args.my_kwarg` |
    | `rq_job.func`   | `rq.job.func`                |                        |
    | `queue.name`    | `messaging.destination.name` |                        |
    | `rq_job.id`     | `messaging.message.id`       |                        |

    Note that `rq.job.args`, `rq.job.kwargs`, and `rq.job.func` are serialized and not the actual objects on the job.

  - The AWS Lambda integration doesn't add the `aws_event` and `aws_context` objects anymore. Instead, the following, if available, is accessible:

    | AWS property                                | Sampling context key(s)         |
    | ------------------------------------------- | ------------------------------- |
    | `aws_event["httpMethod"]`                   | `http.request.method`           |
    | `aws_event["queryStringParameters"]`        | `url.query`                     |
    | `aws_event["path"]`                         | `url.path`                      |
    | full URL                                    | `url.full`                      |
    | `aws_event["headers"]["X-Forwarded-Proto"]` | `network.protocol.name`         |
    | `aws_event["headers"]["Host"]`              | `server.address`                |
    | `aws_context["function_name"]`              | `faas.name`                     |
    | `aws_event["headers"]`                      | `http.request.headers.{header}` |

  - The GCP integration doesn't add the `gcp_env` and `gcp_event` keys anymore. Instead, the following, if available, is accessible:

    | Old sampling context key          | New sampling context key       |
    | --------------------------------- | ------------------------------ |
    | `gcp_env["function_name"]`        | `faas.name`                    |
    | `gcp_env["function_region"]`      | `faas.region`                  |
    | `gcp_env["function_project"]`     | `gcp.function.project`         |
    | `gcp_env["function_identity"]`    | `gcp.function.identity`        |
    | `gcp_env["function_entry_point"]` | `gcp.function.entry_point`     |
    | `gcp_event.method`                | `http.request.method`          |
    | `gcp_event.query_string`          | `url.query`                    |
    | `gcp_event.headers`               | `http.request.header.{header}` |

#### Internals

- The `sentry_sdk.Scope()` constructor no longer accepts a `client` parameter.
- `sentry_sdk.init` now returns `None` instead of a context manager.

### Removed

#### General

- Dropped support for Python 3.6.
- `set_measurement` has been removed.
- Setting `Scope.user` directly is no longer supported. Use `Scope.set_user()` instead.

#### Tracing

- The `enable_tracing` `init` option has been removed. Configure `traces_sample_rate` directly.
- The `propagate_traces` `init` option has been removed. Use `trace_propagation_targets` instead.
- The `custom_sampling_context` parameter of `start_transaction` has been removed. Use `attributes` instead to set key-value pairs of data that should be accessible in the traces sampler. Note that span attributes need to conform to the [OpenTelemetry specification](https://opentelemetry.io/docs/concepts/signals/traces/#attributes), meaning only certain types can be set as values.
- When setting span status, the HTTP status code is no longer automatically added as a tag.
- `start_transaction` is deprecated and no longer takes the following arguments:
  - `trace_id`, `baggage`: use `continue_trace` for propagation from headers or environment variables
  - `same_process_as_parent`
  - `span_id`
  - `parent_span_id`: you can supply a `parent_span` instead
- The `Scope.transaction` property has been removed. To obtain the root span (previously transaction), use `Scope.root_span`. To set the root span's (transaction's) name, use `Scope.set_transaction_name()`.
- The `Scope.span =` setter has been removed. Please use the new `span.activate()` api instead if you want to activate a new span manually instead of using the `start_span` context manager.
- `span.containing_transaction` has been removed. Use `span.root_span` instead.
- `continue_from_headers`, `continue_from_environ` and `from_traceparent` have been removed, please use top-level API `sentry_sdk.continue_trace` instead.
- `Baggage.populate_from_transaction` has been removed.

#### Integrations

- PyMongo: The integration no longer sets tags. The data is still accessible via span attributes.
- PyMongo: The integration doesn't set `operation_ids` anymore. The individual IDs (`operation_id`, `request_id`, `session_id`) are now accessible as separate span attributes.
- Django: Dropped support for Django versions below 2.0.
- trytond: Dropped support for trytond versions below 5.0.
- Falcon: Dropped support for Falcon versions below 3.0.
- eventlet: Dropped support for eventlet completely.
- Threading: The integration no longer takes the `propagate_hub` argument.
- Starlette: Passing a list or `None` for `failed_request_status_codes` is no longer supported. Pass a set of integers instead.

#### Profiling

- `profiles_sample_rate` and `profiler_mode` were removed from options available via `_experiments`. Use the top-level `profiles_sample_rate` and `profiler_mode` options instead.

#### Transport

- `Transport.capture_event` has been removed. Use `Transport.capture_envelope` instead.
- Function transports are no longer supported. Subclass the `Transport` instead.

#### Sessions

- The context manager `auto_session_tracking()` has been removed. Use `track_session()` instead.
- The context manager `auto_session_tracking_scope()` has been removed. Use `track_session()` instead.
- Utility function `is_auto_session_tracking_enabled()` has been removed. There is no public replacement. There is a private `_is_auto_session_tracking_enabled()` (if you absolutely need this function) It accepts a `scope` parameter instead of the previously used `hub` parameter.
- Utility function `is_auto_session_tracking_enabled_scope()` has been removed. There is no public replacement. There is a private `_is_auto_session_tracking_enabled()` (if you absolutely need this function).

#### Metrics

- `sentry_sdk.metrics` and associated metrics APIs have been removed as Sentry no longer accepts metrics data in this form. See https://sentry.zendesk.com/hc/en-us/articles/26369339769883-Upcoming-API-Changes-to-Metrics
- The experimental options `enable_metrics`, `before_emit_metric` and `metric_code_locations` have been removed.

#### Internals

- Class `Hub` has been removed.
- Class `_ScopeManager` has been removed.
- `PropagationContext` constructor no longer takes a `dynamic_sampling_context` but takes a `baggage` object instead.
- Setting `scope.level` has been removed. Use `scope.set_level` instead.
- `debug.configure_debug_hub` was removed.
- The `span` argument of `Scope.trace_propagation_meta` is no longer supported.


### Deprecated

- `sentry_sdk.start_transaction()` is deprecated. Use `sentry_sdk.start_span()` instead.
- `Span.set_data()` is deprecated. Use `Span.set_attribute()` instead.


---------------------------------------------------------------------------------


## Upgrading to 2.0

Looking to upgrade from Sentry SDK 1.x to 2.x? Here's a comprehensive list of what's changed. Looking for a more digestible summary? See the [guide in the docs](https://docs.sentry.io/platforms/python/migration/1.x-to-2.x) with the most common migration patterns.

### New Features

- Additional integrations will now be activated automatically if the SDK detects the respective package is installed: Ariadne, ARQ, asyncpg, Chalice, clickhouse-driver, GQL, Graphene, huey, Loguru, PyMongo, Quart, Starlite, Strawberry.
- While refactoring the [inner workings](https://docs.sentry.io/platforms/python/enriching-events/scopes/) of the SDK we added new top-level APIs for custom instrumentation called `new_scope` and `isolation_scope`. See the [Deprecated](#deprecated) section to see how they map to the existing APIs.

### Changed

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
- `sentry_sdk.tracing_utils.get_current_span()` now takes a `scope` instead of a `hub` as parameter.
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

### Removed

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

### Deprecated

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

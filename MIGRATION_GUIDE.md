# Sentry SDK Migration Guide

## Upgrading to 3.0

Looking to upgrade from Sentry SDK 2.x to 3.x? Here's a comprehensive list of what's changed. Looking for a more digestible summary? See the [guide in the docs](https://docs.sentry.io/platforms/python/migration/2.x-to-3.x) with the most common migration patterns.

### New Features

### Changed

- The SDK now supports Python 3.7 and higher.
- `sentry_sdk.start_span` now only takes keyword arguments.
- `sentry_sdk.start_transaction`/`sentry_sdk.start_span` no longer takes the following arguments: `span`, `parent_sampled`, `trace_id`, `span_id` or `parent_span_id`.
- You can no longer change the sampled status of a span with `span.sampled = False` after starting it.
- The `Span()` constructor does not accept a `hub` parameter anymore.
- `Span.finish()` does not accept a `hub` parameter anymore.
- `Span.finish()` no longer returns the `event_id` if the event is sent to sentry.
- The `Profile()` constructor does not accept a `hub` parameter anymore.
- A `Profile` object does not have a `.hub` property anymore.
- `sentry_sdk.continue_trace` no longer returns a `Transaction` and is now a context manager.
- Redis integration: In Redis pipeline spans there is no `span["data"]["redis.commands"]` that contains a dict `{"count": 3, "first_ten": ["cmd1", "cmd2", ...]}` but instead `span["data"]["redis.commands.count"]` (containing `3`) and `span["data"]["redis.commands.first_ten"]` (containing `["cmd1", "cmd2", ...]`).
- clickhouse-driver integration: The query is now available under the `db.query.text` span attribute (only if `send_default_pii` is `True`).
- `sentry_sdk.init` now returns `None` instead of a context manager.
- The `sampling_context` argument of `traces_sampler` and `profiles_sampler` now additionally contains all span attributes known at span start.
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

  -The RQ integration doesn't add the `rq_job` object anymore. Instead, the individual properties of the job and the queue, if available, are accessible as follows:

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


### Removed

- Spans no longer have a `description`. Use `name` instead.
- Dropped support for Python 3.6.
- The `custom_sampling_context` parameter of `start_transaction` has been removed. Use `attributes` instead to set key-value pairs of data that should be accessible in the traces sampler. Note that span attributes need to conform to the [OpenTelemetry specification](https://opentelemetry.io/docs/concepts/signals/traces/#attributes), meaning only certain types can be set as values.
- The PyMongo integration no longer sets tags. The data is still accessible via span attributes.
- The PyMongo integration doesn't set `operation_ids` anymore. The individual IDs (`operation_id`, `request_id`, `session_id`) are now accessible as separate span attributes.
- `sentry_sdk.metrics` and associated metrics APIs have been removed as Sentry no longer accepts metrics data in this form. See https://sentry.zendesk.com/hc/en-us/articles/26369339769883-Upcoming-API-Changes-to-Metrics
- The experimental options `enable_metrics`, `before_emit_metric` and `metric_code_locations` have been removed.
- When setting span status, the HTTP status code is no longer automatically added as a tag.
- Class `Hub` has been removed.
- Class `_ScopeManager` has been removed.
- The context manager `auto_session_tracking()` has been removed. Use `track_session()` instead.
- The context manager `auto_session_tracking_scope()` has been removed. Use `track_session()` instead.
- Utility function `is_auto_session_tracking_enabled()` has been removed. There is no public replacement. There is a private `_is_auto_session_tracking_enabled()` (if you absolutely need this function) It accepts a `scope` parameter instead of the previously used `hub` parameter.
- Utility function `is_auto_session_tracking_enabled_scope()` has been removed. There is no public replacement. There is a private `_is_auto_session_tracking_enabled()` (if you absolutely need this function)
- Setting `scope.level` has been removed. Use `scope.set_level` instead.
- `span.containing_transaction` has been removed. Use `span.root_span` instead.
- `continue_from_headers`, `continue_from_environ` and `from_traceparent` have been removed, please use top-level API `sentry_sdk.continue_trace` instead.
- `PropagationContext` constructor no longer takes a `dynamic_sampling_context` but takes a `baggage` object instead.
- `ThreadingIntegration` no longer takes the `propagate_hub` argument.
- `Baggage.populate_from_transaction` has been removed.

### Deprecated

- `sentry_sdk.start_transaction` is deprecated. Use `sentry_sdk.start_span` instead.

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

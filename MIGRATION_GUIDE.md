# Sentry SDK 3.0 Migration Guide

Looking to upgrade from Sentry SDK 2.x to 3.x? Here's a comprehensive list of what's changed. Looking for a more digestable summary? See the [guide in the docs](https://docs.sentry.io/platforms/python/migration/2.x-to-3.x) with the most common migration patterns.

## Changed

### API

- `sentry_sdk.init()` can no longer be used as a context manager.
- The `@trace` decorator doesn't accept a `template` parameter anymore.
- The option `attach_stacktrace` is now `True` by default, meaning the SDK will attach stack traces to messages.
- `add_attachment` no longer accepts an `add_to_transactions` argument.
- If a `before_breadcrumb` callback, an event processor, or an error processor raises an exception, the breadcrumb or event is now dropped. Previously, the SDK would log the exception and continue processing the unmodified breadcrumb or event.
- The top-level manual tracing API is now wired to span streaming tracing:
  - `sentry_sdk.start_transaction` doesn't exist anymore. Use `sentry_sdk.start_span` instead.
  - `sentry_sdk.start_span` accepts five arguments now: `name` (mandatory), `attributes`, `parent_span` (default: current active span), `active` (default: `True`), `links` (spans to link to without making them the parent; more can be added with `span.add_link()` / `span.add_links()`).
  - The `@trace` decorator accepts three optional arguments: `name`, `attributes`, `active` (default: `True`).
  - `sentry_sdk.continue_trace` accepts a single dictionary with the incoming headers or environment data. It is no longer a context manager and it does not return a span/transaction anymore. A span needs to be started manually afterwards.
  - `sentry_sdk.new_trace` resets the trace.

### Integrations

- **The Strawberry integration won't auto-enable anymore** if we detect `strawberry-graphql` is installed. Set it up manually, setting the `async_execution` integration option to either `True` or `False` depending on if your app is async or sync.

  ```python
  from sentry_sdk.integrations.strawberry import StrawberryIntegration

  sentry_sdk.init(
      integrations=[
          StrawberryIntegration(async_execution=True),  # or False
      ],
      ...
  )
  ```

- The FastAPI and Starlette integrations no longer eagerly consume the request body. As a result, the body is only reported in events if your handler parsed it with `Request.json()` or `Request.form()` before the event is captured.
- The `UnraisableHookIntegration` is now enabled by default.
- We now don't suppress chained exceptions in the ASGI and asyncio integrations by default. The related `suppress_asgi_chained_exceptions` experimental option was removed.
- In the AWS Lambda and GCP integrations, the text of the message of the warning the SDK optionally emits if a function is about to time out has changed.
- The `http.request.header.<name>` span attributes set by server integrations are now lists of strings instead of strings.
- The HTTP breadcrumbs created by the stdlib (`http.client`) and boto3 integrations now use different keys: `http.method` is now `http.request.method`, `url` (or `aws.request.url` for boto3) is now `url.full`, `http.query` is now `url.query`, and `http.fragment` is now `url.fragment`. The stdlib breadcrumb no longer contains `reason`. The query string is filtered according to `data_collection`'s `url_query_params` setting.
- The `url` field of HTTP breadcrumbs created by the HTTPX, httpx2, pyreqwest, and AIOHTTP client integrations now contains the full URL, including the query string and fragment. The query string is filtered according to `data_collection`'s `url_query_params` setting.
- The breadcrumbs created by the PyMongo integration now use different keys: `db.name` is now `db.namespace`, `db.system` is now `db.system.name`, `db.operation` is now `db.operation.name`, `db.mongodb.collection` is now `db.collection.name`, and `net.peer.name` and `net.peer.port` are now `server.address` and `server.port`.
- The connection breadcrumbs created by the asyncpg and aiomysql integrations now use `db.system.name` instead of `db.system` and `db.namespace` instead of `db.name`.
- The ClickHouse integration no longer records query parameters (`db.params`) or the query ID (`db.query_id`) on spans and breadcrumbs.
- The `RustTracingIntegration` no longer includes the values of tracing fields if `send_default_pii` is enabled. Pass `include_tracing_fields=True` to the integration to include them.
- Whether the Dramatiq integration attaches message bodies to events is now controlled by `data_collection`'s `queues` setting instead of `http_bodies`.
- The Celery integration no longer sets the status of task spans to error when a task raises a control flow exception (`Retry`, `Ignore`, or `Reject`).
- The boto3 integration now names spans `<ServiceID>.<Operation>` (for example, `S3.GetObject` or `DynamoDB.GetItem`), following OpenTelemetry semantic conventions. Previously, spans were named `aws.<service-id>.<Operation>` (for example, `aws.s3.GetObject`). Its spans, including the span for reading a streamed response body, now also have the origin `auto.aws.boto3` instead of `auto.http.boto3`. Update any alerts, dashboards, or `ignore_spans` rules that match on the old names or origin.
- Enabling an AI agent integration (LangChain, OpenAI Agents, or Pydantic AI) no longer disables the OpenAI, Anthropic, and Google GenAI integrations. Instead, those integrations don't create chat spans for requests made from within an agent framework's chat span, but still create other spans, such as embedding spans. The LangChain integration no longer instruments embeddings directly; embedding spans are now created by the integration of the underlying client library.
- Whether the MCP integration records tool arguments, tool results, and prompt contents is now controlled by `data_collection`'s new `mcp` setting (`{"inputs": bool, "outputs": bool}`, both `True` by default) instead of the `gen_ai` setting.
- With `handled_tool_call_exceptions=True` (the default), the Pydantic AI integration now reports an error for every failed tool argument validation attempt, including retries. Previously, only one error was reported per tool call.
- The Pydantic AI integration no longer sets the `gen_ai.response.streaming` span attribute.

#### Span operation consolidation

Span ops are now a smaller, framework-neutral set.

- The OpenAI integration now emits `gen_ai.chat` instead of `gen_ai.responses`.
- The Cohere integration now emits `gen_ai.chat` instead of `ai.chat_completions.create.cohere`, and `gen_ai.embeddings` instead of `ai.embeddings.create.cohere`.
- The TypeSafe AI integration now emits `gen_ai.decide` instead of `gen_ai.evaluate`.
- The Redis integration now emits `db.query` instead of `db.redis`.
- The Django integration now emits `view.render` instead of `view.response.render`, `view` instead of `view.authenticate`, and `function` instead of `event.django`.
- The Django, Starlette, Starlite, and Litestar integrations now emit `middleware` instead of `middleware.django`, `middleware.starlette`, `middleware.starlette.receive`, `middleware.starlette.send`, `middleware.starlite`, `middleware.starlite.receive`, `middleware.starlite.send`, `middleware.litestar`, `middleware.litestar.receive`, and `middleware.litestar.send`.
- The ARQ, Celery, Django, Dramatiq, Huey, RQ, and Ray integrations now emit `queue.publish` and `queue.process` instead of `queue.submit.arq`, `queue.task.arq`, `queue.submit.celery`, `queue.task.celery`, `queue.submit.django`, `queue.task.dramatiq`, `queue.submit.huey`, `queue.task.huey`, `queue.task.rq`, `queue.submit.ray`, and `queue.task.ray`.
- The Boto3 integration now emits `rpc` instead of `http.client` for AWS API calls.

#### GenAI span attributes

Deprecated GenAI span attributes were replaced with their current counterparts.

| Old attribute | New attribute |
| --- | --- |
| `gen_ai.system` | `gen_ai.provider.name` |
| `gen_ai.usage.input_tokens.cached` | `gen_ai.usage.cache_read.input_tokens` |
| `gen_ai.usage.input_tokens.cache_write` | `gen_ai.usage.cache_creation.input_tokens` |
| `gen_ai.usage.output_tokens.reasoning` | `gen_ai.usage.reasoning.output_tokens` |
| `gen_ai.response.time_to_first_token` | `gen_ai.response.time_to_first_chunk` |
| `gen_ai.tool.input` | `gen_ai.tool.call.arguments` |
| `gen_ai.tool.output` | `gen_ai.tool.call.result` |
| `mcp.tool.name` | `gen_ai.tool.name` |
| `mcp.prompt.name` | `gen_ai.prompt.name` |
| `mcp.tool.result.content` | `gen_ai.tool.call.result` |
| `mcp.tool.result.content_count` | removed |
| `gen_ai.request.available_tools` (LangChain only) | `gen_ai.tool.definitions` |

Additionally, the OpenAI integration now sets `gen_ai.operation.name` to `chat` instead of `responses` for Responses API calls.

#### GenAI spans

- The OpenAI Agents integration no longer creates a workflow span (`<agent name> workflow`) wrapping the agent run, or handoff spans (`handoff from <agent> to <agent>`). Agents that hand off to each other now appear as sibling `invoke_agent` spans in the same trace.
- The LangGraph integration no longer creates a `create_agent` span when a graph is compiled.
- Invoke Agent spans created by the LangChain, LangGraph, OpenAI Agents, and Pydantic AI integrations no longer contain information about the model, request parameters, available tools, streaming, or token usage. This information is available on the child AI Client spans instead. Execute Tool spans created by the OpenAI Agents integration no longer contain model and request information either.

#### Django

- The Django integration now creates spans for cache operations by default. Pass `DjangoIntegration(cache_spans=False)` to `sentry_sdk.init()` to turn them off.
- The Django integration no longer force-enables cache spans when Spotlight is active and `settings.DEBUG` is `True`. The `cache_spans` option is now always respected as given.

#### Logging

- **The standard library logging integration is not auto-enabled by default anymore.** To continue using it, add it to the `integrations` list in your `sentry_sdk.init()`:

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
- The `capture_sentry_logs` option was removed. Use `level=None` to disable log capture.
- The `ignore_logger` helper was renamed to `ignore_logger_for_events`.
- The `ignore_logger_for_sentry_logs` helper was renamed to `ignore_logger`.
- `SentryHandler` was removed. Use `EventHandler` instead.
- `SentryLogsHandler` was renamed to `LogsHandler`.
- The `DEFAULT_EVENT_LEVEL` constant was removed.
- When you enable the integration by adding `LoggingIntegration` to your `sentry_sdk.init()`, it'll start capturing Sentry logs and breadcrumbs. Creating events from logs can be enabled by providing additional integration options.

  | Old name | New name | Old default | New default | Description |
  | --- | --- | --- | --- | --- |
  | `level` | `breadcrumb_level` | `INFO` | `INFO` | Captures logs of that level and higher as breadcrumbs. |
  | `event_level` | `event_level` | `ERROR` | `None` | Captures logs of that level and higher as events. |
  | `sentry_logs_level` | `level` | `INFO` | `INFO` | Captures logs of that level and higher as Sentry logs. |
  | `capture_sentry_logs` | removed | `False` | n/a | Allows to opt out of instrumenting logs as Sentry logs. Use `level` (previously `sentry_logs_level`) to adjust what should be captured instead. |
  | `ignore_logger` | `ignore_logger_for_events` | n/a | n/a | Loggers that match this name will not create breadcrumbs and events. |
  | `unignore_logger` | `unignore_logger_for_events` | n/a | n/a | Loggers that match this name will create breadcrumbs and events again. |
  | `ignore_logger_for_sentry_logs` | `ignore_logger` | n/a | n/a | Loggers that match this name will not create Sentry logs. |
  | `unignore_logger_for_sentry_logs` | `unignore_logger` | n/a | n/a | Loggers that match this name will create Sentry logs again. |


#### Loguru

- **The Loguru logging integration is not auto-enabled by default anymore** if you have Loguru installed. To continue using it, add it to the `integrations` list in your `sentry_sdk.init()`:

  ```python
  import sentry_sdk
  from sentry_sdk.integrations.loguru import LoguruIntegration

  sentry_sdk.init(
      integrations=[
          LoguruIntegration(),
      ]
  )
  ```

- The `level` integration option is now called `breadcrumb_level`.
- The `sentry_logs_level` integration option is now called `level`.
- The `capture_sentry_logs` option was removed. Use `level=None` to disable log capture.
- The `loguru_sentry_logs_handler` function was renamed to `loguru_handler`.
- The order of the `LoguruIntegration` constructor arguments changed. If you were passing arguments positionally, switch to keyword arguments.
- When you enable the integration by adding `LoguruIntegration` to your `sentry_sdk.init()`, it'll start capturing Sentry logs and breadcrumbs. Creating events from logs can be enabled by providing additional integration options.

  | Old name | New name | Old default | New default | Description |
  | --- | --- | --- | --- | --- |
  | `level` | `breadcrumb_level` | `INFO` | `INFO` | Captures logs of that level and higher as breadcrumbs. |
  | `event_level` | `event_level` | `ERROR` | `None` | Captures logs of that level and higher as events. |
  | `sentry_logs_level` | `level` | `INFO` | `INFO` | Captures logs of that level and higher as Sentry logs. |
  | `capture_sentry_logs` | removed | `False` | n/a | Allows to opt out of instrumenting logs as Sentry logs. Use `level` (previously `sentry_logs_level`) to adjust what should be captured instead. |

### Miscellaneous

- Exception groups in exception chains are now properly unfurled.
- The `DedupeIntegration` now marks each exception it has seen and drops any subsequent event for the same exception instance, regardless of where it's captured. Previously, only consecutive captures of the same exception in the same context were deduplicated. Note that this also applies if the first event was dropped, for example by `before_send` or sampling. The `DedupeIntegration.reset_last_seen` method was removed.
- We changed the way we emit warnings. Deprecations will from now on be always emitted using `warnings.warn()`, while all other warnings will be emitted using `logger.warning()`.

## Removed

### Supported Versions

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
- Dropped support for Celery below 5.0.
- Dropped support for SQLAlchemy below 1.4.
- Dropped support for Huey below 2.0.
- Dropped support for Litestar below 2.0.
- Dropped support for PySpark below 3.0.
- Dropped support for redis-py below 3.4.1.
- Dropped support for Pydantic AI below 1.76.
- Dropped support for OpenAI Agents below 0.10.3.
- Dropped support for MCP below 2.0.
- The dedicated instrumentation for the standalone `fastmcp` package was removed. `fastmcp` servers are now instrumented via the underlying `mcp` package, so only `fastmcp` versions that are compatible with `mcp>=2.0` are supported.
- Dropped support for Hugging Face Hub below 2.0.
- The following integrations now enforce their minimum supported version and won't be enabled if an older version of the package is installed:
  - Apache Beam 2.12
  - Cohere 5.4.0
  - Dramatiq 1.9
  - Google GenAI 1.29.0
  - gRPC 1.32.0
  - HTTPX 0.16.0
  - LangChain 0.1.0
  - LangGraph 0.6.6
  - LaunchDarkly 9.8.0
  - LiteLLM 1.77.5
  - Loguru 0.7.0
  - OpenAI 1.0.0
  - OpenFeature 0.7.1
  - PyMongo 3.5.0
  - pyreqwest 0.11.6
  - Starlite 1.48
  - Typer 0.15
  - Unleash 6.0.1

### API

- The `send_default_pii` option was removed. Use `data_collection` instead, which gives you granular control over what data the SDK collects. Note that the `data_collection` defaults collect more data than `send_default_pii=False` did. See [Migrating from `send_default_pii` to `data_collection`](#migrating-from-send_default_pii-to-data_collection) for how to restore the previous behaviour.
- The `enable_tracing` option was removed. Use `traces_sample_rate=1.0` instead.
- The `enable_logs` option was removed. Using Sentry's logging API now works without requiring setting `enable_logs=True`.
- The `enable_metrics` option was removed.
- The deprecated `@ai_track` decorator was removed.
- The deprecated `record_token_usage` function was removed.
- The `sentry_sdk.ai.monitoring` module was removed, including the `set_ai_pipeline_name` and `get_ai_pipeline_name` functions.
- The deprecated `push_scope` and `configure_scope` APIs have been removed. Use `with new_scope():` to push a new scope and `scope = get_current_scope()` to retrieve the current scope instead.
- The experimental `max_spans` option was removed.
- The experimental `before_send_log` option was removed. Use the top-level `before_send_log` instead.
- The experimental `before_send_metric` option was removed. Use the top-level `before_send_metric` instead.
- The experimental `ignore_spans` option was removed. Use the top-level `ignore_spans` instead.
- The experimental `before_send_span` option was removed. Use the top-level `before_send_span` instead.
- `before_send_transaction` was removed alongside transaction-based tracing. Use `before_send_span` instead to modify a span before it's sent. Note that `before_send_span` can't drop spans. Use `ignore_spans` for that.
- Removed the `auto_session_tracking` decorator, as well as `auto_session_tracking_scope`, `is_auto_session_tracking_enabled`, and `is_auto_session_tracking_enabled_scope`. Use `track_session` instead.
- The deprecated `set_measurement` API was removed.
- The `include_local_variables` option was removed. Use `data_collection`'s `stack_frame_variables` as a drop-in replacement.
- The `include_source_context` option was removed. Use `data_collection`'s `frame_context_lines` for more granular control over the source context reported by specifying the number of lines to include around the failing line, or set `frame_context_lines=0` to disable source context entirely.
- The `event_scrubber` option and the `sentry_sdk.scrubber` module (`EventScrubber`, `DEFAULT_DENYLIST`, `DEFAULT_PII_DENYLIST`) were removed. Use `data_collection` to control what data is collected, and `before_send` for custom scrubbing. Note that local variables in stack frames are no longer scrubbed by default. To filter out sensitive variables (such as `password` or `token`), set `data_collection`'s `stack_frame_variables` to `{"mode": "denylist"}`. You can add your own terms with `"terms": [...]`.
- Transaction profiling and related code was removed, including the `profiles_sample_rate` and `profiles_sampler` options and the `sleep` value of `profiler_mode`. Use continuous profiling instead, configured with `profile_session_sample_rate` and `profile_lifecycle`. The transaction-profiler exports from `sentry_sdk.profiler` (`Profile`, `Scheduler`, `ThreadScheduler`, `GeventScheduler`, `setup_profiler`, `teardown_profiler`, `has_profiling_enabled`, `MAX_PROFILE_DURATION_NS`, `PROFILE_MINIMUM_SAMPLES`) were removed as well.
- The `start_profile_session` and `stop_profile_session` were removed in favor of `start_profiler` and `stop_profiler`, respectively.
- The experimental `continuous_profiling_mode` option was removed. Use the top-level `profiler_mode`, instead.
- Removed the deprecated Hub class and all uses of hub throughout the SDK in arguments, options, etc. Use a scope instead.
- The `data_collection` option is no longer accessible under `_experiments`. Use it as a top-level option instead.
- The experimental `record_sql_params` option was removed. Use the `database_query_data` setting of `data_collection` instead.
- The deprecated `propagate_traces` option has been removed. Use `trace_propagation_targets` instead, which gives you more power over trace propagation. Note that only the top-level `init` option was removed; the `propagate_traces` option of the Celery integration remains available.
- The `trace_ignore_status_codes` option was removed.
- The `update_current_span` API was removed.
- The `sentry_sdk.tracing` module was removed, including the `Span`, `Transaction`, `NoOpSpan`, and `TransactionSource` classes. Import tracing primitives from `sentry_sdk.traces` instead. `TransactionSource` was replaced by `sentry_sdk.traces.SegmentNameSource`, which has the same values. The span classes from 2.x's span streaming mode, `sentry_sdk.traces.StreamedSpan` and `NoOpStreamedSpan`, are now called `Span` and `NoOpSpan`; the old names remain available as aliases.
- The `stream_gen_ai_spans` option was removed. All spans are streamed now. If you previously set `stream_gen_ai_spans=False`, note that GenAI integrations no longer truncate messages (previously, only the last message was kept and truncated), and the OpenAI, Anthropic, LangChain, LiteLLM, and OpenAI Agents integrations no longer replace binary content in messages (for example, base64-encoded images) with `[Filtered]`. To prevent prompts and responses from being sent, set `data_collection`'s `gen_ai` `inputs` and/or `outputs` to `False`.
- Span streaming is now the only tracing mode. The experimental `trace_lifecycle` option was removed. The top-level `trace_lifecycle` option is still accepted, but any value other than `"stream"` is ignored and logs a warning.

### Integrations

- Removed the `transaction_style` option from server integrations (`DjangoIntegration`, `StarletteIntegration`, `FastApiIntegration`, `AioHttpIntegration`, `FlaskIntegration`, `PyramidIntegration`, `QuartIntegration`, `BottleIntegration`, `FalconIntegration`, `StarliteIntegration`, `LitestarIntegration` and `SentryAsgiMiddleware`). Segments are now always named after the route pattern (for example, `/users/<id>`). For the Flask, Quart, Bottle, Pyramid, and AIOHTTP integrations, this changes the default segment names, which were previously based on the endpoint, route, or handler name. Update any alerts, dashboards, or `ignore_spans` rules that match on the old names.
- Removed the `include_prompts` option from GenAI integrations (`AnthropicIntegration`, `CohereIntegration`, `GoogleGenAIIntegration`, `HuggingfaceHubIntegration`, `LangchainIntegration`, `LanggraphIntegration`, `LiteLLMIntegration`, `MCPIntegration`, `OpenAIIntegration` and `PydanticAIIntegration`)
- Removed the `LangchainIntegration`'s `max_spans` option.
- Removed the `RedisIntegration`'s `max_data_size` option.
- Removed the possibility to supply a specific client to the `LaunchDarklyIntegration`.
- The `SentrySpanProcessor`, `SentryPropagator`, `instrumenter`, and associated OpenTelemetry compatibility code was removed along with the `opentelemetry` extra and the `SentryPropagator` entrypoint. Use the `OTLPIntegration` instead.
- The experimental option `otel_powered_performance` has been removed together with the associated `OpenTelemetryIntegration` and `opentelemetry-experimental` extra.
- The `failed_request_status_codes` integration option now only supports a set of integers as input. Lists of integers or containers of integers are no longer supported.
- Removed Spotlight integration for Django. The SDK no longer injects `sentry_sdk.spotlight.SpotlightMiddleware` into your Django middleware when Spotlight is enabled and `DEBUG` is `True`, and the middleware itself was removed. The `SENTRY_SPOTLIGHT_ON_ERROR` and `SENTRY_SPOTLIGHT_MIDDLEWARE` environment variables have no effect anymore. See [Spotlight 2.0](https://github.com/getsentry/spotlight/issues/891) for more context.
- The deprecated parameter `propagate_hub` in `ThreadingIntegration()` was removed.
- The `unsafe_context_data` parameter of `SentryAsgiMiddleware` was removed. If you were passing arguments to `SentryAsgiMiddleware` positionally, switch to keyword arguments, since the position of the remaining parameters has changed.
- The `max_spans` option of the `LangchainIntegration` was removed.
- `SanicIntegration` no longer accepts `unsampled_statuses`.
- The `hash_query` method of the Strawberry integration's `SentryAsyncExtension` and `SentrySyncExtension` was removed, along with the `graphql.resource_name` span attribute.
- The `record_params` option of `AsyncPGIntegration` and `AioMySQLIntegration` was removed. Use the `database_query_data` setting of `data_collection` instead.

### Miscellaneous

- A number of extras (installable via `sentry-sdk[extra-name]`) has been removed. Use the base package (`sentry-sdk`) instead; there is no difference in functionality. The following extras have been removed: `aiohttp`, `anthropic`, `arq`, `asyncpg`, `beam`, `bottle`, `celery`, `celery-redbeat`, `chalice`, `clickhouse-driver`, `django`, `falcon`, `fastapi`, `google-genai`, `httpx`, `huey`, `huggingface_hub`, `langchain`, `langgraph`, `launchdarkly`, `litellm`, `litestar`, `loguru`, `mcp`, `openai`, `openfeature`, `pydantic_ai`, `pymongo`, `pyspark`, `rq`, `sanic`, `sqlalchemy`, `starlette`, `starlite`, `statsig`, `tornado`, `unleash`.
- `configure_debug_hub` was removed.
- `Baggage.from_options` was removed.
- `Transport.capture_event` was removed. Use `Transport.capture_envelope` instead.
- `Envelope.add_transaction`, `Envelope.add_profile`, `Envelope.get_transaction_event`, and `Item.get_transaction_event` were removed.
- `sentry_sdk.utils.event_from_exception` no longer accepts a `client_options` argument. The current client's options are used instead. If you were passing arguments positionally, note that `mechanism` is now the second positional argument.
- Function transports were removed.
- The `Scope.trace_propagation_meta` function no longer accepts a `span` as argument.
- Direct assignment to `Scope.level` was removed. Use `sentry_sdk.set_level` instead.
- Direct assignment to `Scope.user` was removed. Use `sentry_sdk.set_user` instead.
- `Scope.iter_headers` was removed.
- `Scope.start_transaction`, `Scope.start_streamed_span`, and `Scope.continue_trace` were removed. Use the top-level `sentry_sdk.start_span` and `sentry_sdk.continue_trace` instead.
- The `SPANSTATUS`, `SPANTEMPLATE`, and `INSTRUMENTER` constants were removed from `sentry_sdk.consts`.
- The SDK won't set any tags on its own anymore.

## Migration

### Migrating from `send_default_pii` to `data_collection`

The new `data_collection` defaults collect more data than `send_default_pii` did. User identity, generative AI content, HTTP request/response bodies, and file paths are now on by default, and more HTTP headers and cookies are collected.

If you wish to restrict data collection to roughly match the previous behaviour of `send_default_pii=False`, you can use the following configuration:

```python
sentry_sdk.init(
    data_collection={
        "user_info": False,
        "http_bodies": [],
        "graphql": {
            "document": False,
            "variables": False,
        },
        "gen_ai": {
            "inputs": False,
            "outputs": False,
        },
        "mcp": {
            "inputs": False,
            "outputs": False,
        },
        "database_query_data": False,
        "queues": False,
        "http_headers": {
            "request": {
                "mode": "denylist",
                "terms": ["forwarded", "-ip", "remote-", "via", "-user"]
            },
        },
        "cookies": { "mode": "denylist", "terms": ["forwarded", "-ip", "remote-", "via", "-user"] },
        "url_query_params": {
            "mode": "denylist", "terms": ["forwarded", "-ip", "remote-", "via", "-user"],
        },
    },
)
```

---------------------------------------------------------------------------------

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

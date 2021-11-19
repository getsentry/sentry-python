# Changelog and versioning

## Versioning Policy

This project follows [semver](https://semver.org/), with three additions:

- Semver says that major version `0` can include breaking changes at any time. Still, it is common practice to assume that only `0.x` releases (minor versions) can contain breaking changes while `0.x.y` releases (patch versions) are used for backwards-compatible changes (bugfixes and features). This project also follows that practice.

- All undocumented APIs are considered internal. They are not part of this contract.

- Certain features (e.g. integrations) may be explicitly called out as "experimental" or "unstable" in the documentation. They come with their own versioning policy described in the documentation.

We recommend to pin your version requirements against `1.x.*` or `1.x.y`.
Either one of the following is fine:

```
sentry-sdk>=0.10.0,<0.11.0
sentry-sdk==0.10.1
```

A major release `N` implies the previous release `N-1` will no longer receive updates. We generally do not backport bugfixes to older versions unless they are security relevant. However, feel free to ask for backports of specific commits on the bugtracker.

## Unreleased

## 1.5.0

- Also record client outcomes for before send #1211
- Add support for implicitly sized envelope items #1229
- Fix integration with Apache Beam 2.32, 2.33 #1233
- Remove Python 2.7 support for AWS Lambda layers in craft config #1241
- Refactor Sanic integration for v21.9 support #1212
- AWS Lambda Python 3.9 runtime support #1239
- Fix "shutdown_timeout" typing #1256

Work in this release contributed by @galuszkak, @kianmeng, @ahopkins, @razumeiko, @tomscytale, and @seedofjoy. Thank you for your contribution!

## 1.4.3

- Turned client reports on by default.

## 1.4.2

- Made envelope modifications in the HTTP transport non observable #1206

## 1.4.1

- Fix race condition between `finish` and `start_child` in tracing #1203

## 1.4.0

- No longer set the last event id for transactions #1186
- Added support for client reports (disabled by default for now) #1181
- Added `tracestate` header handling #1179
- Added real ip detection to asgi integration #1199

## 1.3.1

- Fix detection of contextvars compatibility with Gevent versions >=20.9.0 #1157

## 1.3.0

- Add support for Sanic versions 20 and 21 #1146

## 1.2.0

- Fix for `AWSLambda` Integration to handle other path formats for function initial handler #1139
- Fix for worker to set daemon attribute instead of deprecated setDaemon method #1093
- Fix for `bottle` Integration that discards `-dev` for version extraction #1085
- Fix for transport that adds a unified hook for capturing metrics about dropped events #1100
- Add `Httpx` Integration #1119
- Add support for china domains in `AWSLambda` Integration #1051

## 1.1.0

- Fix for `AWSLambda` integration returns value of original handler #1106
- Fix for `RQ` integration that only captures exception if RQ job has failed and ignore retries #1076
- Feature that supports Tracing for the `Tornado` integration #1060
- Feature that supports wild cards in `ignore_logger` in the `Logging` Integration #1053
- Fix for django that deals with template span description names that are either lists or tuples #1054

## 1.0.0

This release contains a breaking change

- **BREAKING CHANGE**: Feat: Moved `auto_session_tracking` experimental flag to a proper option and removed explicitly setting experimental `session_mode` in favor of auto detecting its value, hence enabling release health by default #994
- Fixed Django transaction name by setting the name to  `request.path_info` rather than `request.path`
- Fix for tracing by getting HTTP headers from span rather than transaction when possible #1035
- Fix for Flask transactions missing request body in non errored transactions #1034
- Fix for honoring the `X-Forwarded-For` header #1037
- Fix for worker that logs data dropping of events with level error #1032

## 0.20.3

- Added scripts to support auto instrumentation of no code AWS lambda Python functions

## 0.20.2

- Fix incorrect regex in craft to include wheel file in pypi release

## 0.20.1

- Fix for error that occurs with Async Middlewares when the middleware is a function rather than a class

## 0.20.0

- Fix for header extraction for AWS lambda/API extraction
- Fix multiple **kwargs type hints # 967
- Fix that corrects AWS lambda integration failure to detect the aws-lambda-ric 1.0 bootstrap #976
- Fix AWSLambda integration: variable "timeout_thread" referenced before assignment #977
- Use full git sha as release name #960
- **BREAKING CHANGE**: The default environment is now production, not based on release
- Django integration now creates transaction spans for template rendering
- Fix headers not parsed correctly in ASGI middleware, Decode headers before creating transaction #984
- Restored ability to have tracing disabled #991
- Fix Django async views not behaving asynchronously
- Performance improvement: supported pre-aggregated sessions

## 0.19.5

- Fix two regressions added in 0.19.2 with regard to sampling behavior when reading the sampling decision from headers.
- Increase internal transport queue size and make it configurable.

## 0.19.4

- Fix a bug that would make applications crash if an old version of `boto3` was installed.

## 0.19.3

- Automatically pass integration-relevant data to `traces_sampler` for AWS, AIOHTTP, ASGI, Bottle, Celery, Django, Falcon, Flask, GCP, Pyramid, Tryton, RQ, and WSGI integrations
- Fix a bug where the AWS integration would crash if event was anything besides a dictionary
- Fix the Django integrations's ASGI handler for Channels 3.0. Thanks Luke Pomfrey!

## 0.19.2

- Add `traces_sampler` option.
- The SDK now attempts to infer a default release from various environment variables and the current git repo.
- Fix a crash with async views in Django 3.1.
- Fix a bug where complex URL patterns in Django would create malformed transaction names.
- Add options for transaction styling in AIOHTTP.
- Add basic attachment support (documentation tbd).
- fix a crash in the `pure_eval` integration.
- Integration for creating spans from `boto3`.

## 0.19.1

- Fix dependency check for `blinker` fixes #858
- Fix incorrect timeout warnings in AWS Lambda and GCP integrations #854

## 0.19.0

- Removed `_experiments.auto_enabling_integrations` in favor of just `auto_enabling_integrations` which is now enabled by default.

## 0.18.0

- **Breaking change**: The `no_proxy` environment variable is now honored when inferring proxy settings from the system. Thanks Xavier Fernandez!
- Added Performance/Tracing support for AWS and GCP functions.
- Fix an issue with Django instrumentation where the SDK modified `resolver_match.callback` and broke user code.

## 0.17.8

- Fix yet another bug with disjoint traces in Celery.
- Added support for Chalice 1.20. Thanks again to the folks at Cuenca MX!

## 0.17.7

- Internal: Change data category for transaction envelopes.
- Fix a bug under Celery 4.2+ that may have caused disjoint traces or missing transactions.

## 0.17.6

- Support for Flask 0.10 (only relaxing version check)

## 0.17.5

- Work around an issue in the Python stdlib that makes the entire process deadlock during garbage collection if events are sent from a `__del__` implementation.
- Add possibility to wrap ASGI application twice in middleware to enable split up of request scope data and exception catching.

## 0.17.4

- New integration for the Chalice web framework for AWS Lambda. Thanks to the folks at Cuenca MX!

## 0.17.3

- Fix an issue with the `pure_eval` integration in interaction with trimming where `pure_eval` would create a lot of useless local variables that then drown out the useful ones in trimming.

## 0.17.2

- Fix timezone bugs in GCP integration.

## 0.17.1

- Fix timezone bugs in AWS Lambda integration.
- Fix crash on GCP integration because of missing parameter `timeout_warning`.

## 0.17.0

- Fix a bug where class-based callables used as Django views (without using Django's regular class-based views) would not have `csrf_exempt` applied.
- New integration for Google Cloud Functions.
- Fix a bug where a recently released version of `urllib3` would cause the SDK to enter an infinite loop on networking and SSL errors.
- **Breaking change**: Remove the `traceparent_v2` option. The option has been ignored since 0.16.3, just remove it from your code.

## 0.16.5

- Fix a bug that caused Django apps to crash if the view didn't have a `__name__` attribute.

## 0.16.4

- Add experiment to avoid trunchating span descriptions. Initialize with `init(_experiments={"smart_transaction_trimming": True})`.
- Add a span around the Django view in transactions to distinguish its operations from middleware operations.

## 0.16.3

- Fix AWS Lambda support for Python 3.8.
- The AWS Lambda integration now captures initialization/import errors for Python 3.
- The AWS Lambda integration now supports an option to warn about functions likely to time out.
- Testing for RQ 1.5
- Flip default of `traceparent_v2`. This change should have zero impact. The flag will be removed in 0.17.
- Fix compatibility bug with Django 3.1.

## 0.16.2

- New (optional) integrations for richer stacktraces: `pure_eval` for additional variables, `executing` for better function names.

## 0.16.1

- Flask integration: Fix a bug that prevented custom tags from being attached to transactions.

## 0.16.0

- Redis integration: add tags for more commands
- Redis integration: Patch rediscluster package if installed.
- Session tracking: A session is no longer considered crashed if there has been a fatal log message (only unhandled exceptions count).
- **Breaking change**: Revamping of the tracing API.
- **Breaking change**: `before_send` is no longer called for transactions.

## 0.15.1

- Fix fatal crash in Pyramid integration on 404.

## 0.15.0

- **Breaking change:** The ASGI middleware will now raise an exception if contextvars are not available, like it is already the case for other asyncio integrations.
- Contextvars are now used in more circumstances following a bugfix release of `gevent`. This will fix a few instances of wrong request data being attached to events while using an asyncio-based web framework.
- APM: Fix a bug in the SQLAlchemy integration where a span was left open if the database transaction had to be rolled back. This could have led to deeply nested span trees under that db query span.
- Fix a bug in the Pyramid integration where the transaction name could not be overridden at all.
- Fix a broken type annotation on `capture_exception`.
- Basic support for Django 3.1. More work is required for async middlewares to be instrumented properly for APM.

## 0.14.4

- Fix bugs in transport rate limit enforcement for specific data categories. The bug should not have affected anybody because we do not yet emit rate limits for specific event types/data categories.
- Fix a bug in `capture_event` where it would crash if given additional kwargs. Thanks to Tatiana Vasilevskaya!
- Fix a bug where contextvars from the request handler were inaccessible in AIOHTTP error handlers.
- Fix a bug where the Celery integration would crash if newrelic instrumented Celery as well.

## 0.14.3

- Attempt to use a monotonic clock to measure span durations in Performance/APM.
- Avoid overwriting explicitly set user data in web framework integrations.
- Allow to pass keyword arguments to `capture_event` instead of configuring the scope.
- Feature development for session tracking.

## 0.14.2

- Fix a crash in Django Channels instrumentation when SDK is reinitialized.
- More contextual data for AWS Lambda (cloudwatch logs link).

## 0.14.1

- Fix a crash in the Django integration when used in combination with Django Rest Framework's test utilities for request.
- Fix high memory consumption when sending a lot of errors in the same process. Particularly noticeable in async environments.

## 0.14.0

- Show ASGI request data in Django 3.0
- New integration for the Trytond ERP framework. Thanks n1ngu!

## 0.13.5

- Fix trace continuation bugs in APM.
- No longer report `asyncio.CancelledError` as part of AIOHTTP integration.

## 0.13.4

- Fix package classifiers to mark this package as supporting Python 3.8. The SDK supported 3.8 before though.
- Update schema sent for transaction events (transaction status).
- Fix a bug where `None` inside request data was skipped/omitted.

## 0.13.3

- Fix an issue with the ASGI middleware that would cause Uvicorn to infer the wrong ASGI versions and call the wrapped application with the wrong argument count.
- Do not ignore the `tornado.application` logger.
- The Redis integration now instruments Redis blaster for breadcrumbs and transaction spans.

## 0.13.2

- Fix a bug in APM that would cause wrong durations to be displayed on non-UTC servers.

## 0.13.1

- Add new global functions for setting scope/context data.
- Fix a bug that would make Django 1.11+ apps crash when using function-based middleware.

## 0.13.0

- Remove an old deprecation warning (behavior itself already changed since a long time).
- The AIOHTTP integration now attaches the request body to crash reports. Thanks to Vitali Rebkavets!
- Add an experimental PySpark integration.
- First release to be tested under Python 3.8. No code changes were necessary though, so previous releases also might have worked.

## 0.12.3

- Various performance improvements to event sending.
- Avoid crashes when scope or hub is racy.
- Revert a change that broke applications using gevent and channels (in the same virtualenv, but different processes).
- Fix a bug that made the SDK crash on unicode in SQL.

## 0.12.2

- Fix a crash with ASGI (Django Channels) when the ASGI request type is neither HTTP nor Websockets.

## 0.12.1

- Temporarily remove sending of SQL parameters (as part of breadcrumbs or spans for APM) to Sentry to avoid memory consumption issues.

## 0.12.0

- Sentry now has a [Discord server](https://discord.gg/cWnMQeA)! Join the server to get involved into SDK development and ask questions.
- Fix a bug where the response object for httplib (or requests) was held onto for an unnecessarily long amount of time.
- APM: Add spans for more methods on `subprocess.Popen` objects.
- APM: Add spans for Django middlewares.
- APM: Add spans for ASGI requests.
- Automatically inject the ASGI middleware for Django Channels 2.0. This will **break your Channels 2.0 application if it is running on Python 3.5 or 3.6** (while previously it would "only" leak a lot of memory for each ASGI request). **Install `aiocontextvars` from PyPI to make it work again.**

## 0.11.2

- Fix a bug where the SDK would throw an exception on shutdown when running under eventlet.
- Add missing data to Redis breadcrumbs.

## 0.11.1

- Remove a faulty assertion (observed in environment with Django Channels and ASGI).

## 0.11.0

- Fix type hints for the logging integration. Thanks Steven Dignam!
- Fix an issue where scope/context data would leak in applications that use `gevent` with its threading monkeypatch. The fix is to avoid usage of contextvars in such environments. Thanks Ran Benita!
- Fix a reference cycle in the `ThreadingIntegration` that led to exceptions on interpreter shutdown. Thanks Guang Tian Li!
- Fix a series of bugs in the stdlib integration that broke usage of `subprocess`.
- More instrumentation for APM.
- New integration for SQLAlchemy (creates breadcrumbs from queries).
- New (experimental) integration for Apache Beam.
- Fix a bug in the `LoggingIntegration` that would send breadcrumbs timestamps in the wrong timezone.
- The `AiohttpIntegration` now sets the event's transaction name.
- Fix a bug that caused infinite recursion when serializing local variables that logged errors or otherwise created Sentry events.

## 0.10.2

- Fix a bug where a log record with non-strings as `extra` keys would make the SDK crash.
- Added ASGI integration for better hub propagation, request data for your events and capturing uncaught exceptions. Using this middleware explicitly in your code will also fix a few issues with Django Channels.
- Fix a bug where `celery-once` was deadlocking when used in combination with the celery integration.
- Fix a memory leak in the new tracing feature when it is not enabled.

## 0.10.1

- Fix bug where the SDK would yield a deprecation warning about `collections.abc` vs `collections`.
- Fix bug in stdlib integration that would cause spawned subprocesses to not inherit the environment variables from the parent process.

## 0.10.0

- Massive refactor in preparation to tracing. There are no intentional breaking changes, but there is a risk of breakage (hence the minor version bump). Two new client options `traces_sample_rate` and `traceparent_v2` have been added. Do not change the defaults in production, they will bring your application down or at least fill your Sentry project up with nonsense events.

## 0.9.5

- Do not use `getargspec` on Python 3 to evade deprecation warning.

## 0.9.4

- Revert a change in 0.9.3 that prevented passing a `unicode` string as DSN to `init()`.

## 0.9.3

- Add type hints for `init()`.
- Include user agent header when sending events.

## 0.9.2

- Fix a bug in the Django integration that would prevent the user from initializing the SDK at the top of `settings.py`.

  This bug was introduced in 0.9.1 for all Django versions, but has been there for much longer for Django 1.6 in particular.

## 0.9.1

- Fix a bug on Python 3.7 where gunicorn with gevent would cause the SDK to leak event data between requests.
- Fix a bug where the GNU backtrace integration would not parse certain frames.
- Fix a bug where the SDK would not pick up request bodies for Django Rest Framework based apps.
- Remove a few more headers containing sensitive data per default.
- Various improvements to type hints. Thanks Ran Benita!
- Add a event hint to access the log record from `before_send`.
- Fix a bug that would ignore `__tracebackhide__`. Thanks Matt Millican!
- Fix distribution information for mypy support (add `py.typed` file). Thanks Ran Benita!

## 0.9.0

- The SDK now captures `SystemExit` and other `BaseException`s when coming from within a WSGI app (Flask, Django, ...)
- Pyramid: No longer report an exception if there exists an exception view for it.

## 0.8.1

- Fix infinite recursion bug in Celery integration.

## 0.8.0

- Add the always_run option in excepthook integration.
- Fix performance issues when attaching large data to events. This is not really intended to be a breaking change, but this release does include a rewrite of a larger chunk of code, therefore the minor version bump.

## 0.7.14

- Fix crash when using Celery integration (`TypeError` when using `apply_async`).

## 0.7.13

- Fix a bug where `Ignore` raised in a Celery task would be reported to Sentry.
- Add experimental support for tracing PoC.

## 0.7.12

- Read from `X-Real-IP` for user IP address.
- Fix a bug that would not apply in-app rules for attached callstacks.
- It's now possible to disable automatic proxy support by passing `http_proxy=""`. Thanks Marco Neumann!

## 0.7.11

- Fix a bug that would send `errno` in an invalid format to the server.
- Fix import-time crash when running Python with `-O` flag.
- Fix a bug that would prevent the logging integration from attaching `extra` keys called `data`.
- Fix order in which exception chains are reported to match Raven behavior.
- New integration for the Falcon web framework. Thanks to Jacob Magnusson!

## 0.7.10

- Add more event trimming.
- Log Sentry's response body in debug mode.
- Fix a few bad typehints causing issues in IDEs.
- Fix a bug in the Bottle integration that would report HTTP exceptions (e.g. redirects) as errors.
- Fix a bug that would prevent use of `in_app_exclude` without setting `in_app_include`.
- Fix a bug where request bodies of Django Rest Framework apps were not captured.
- Suppress errors during SQL breadcrumb capturing in Django integration. Also change order in which formatting strategies are tried.

## 0.7.9

- New integration for the Bottle web framework. Thanks to Stepan Henek!
- Self-protect against broken mapping implementations and other broken reprs instead of dropping all local vars from a stacktrace. Thanks to Marco Neumann!

## 0.7.8

- Add support for Sanic versions 18 and 19.
- Fix a bug that causes an SDK crash when using composed SQL from psycopg2.

## 0.7.7

- Fix a bug that would not capture request bodies if they were empty JSON arrays, objects or strings.
- New GNU backtrace integration parses stacktraces from exception messages and appends them to existing stacktrace.
- Capture Tornado formdata.
- Support Python 3.6 in Sanic and AIOHTTP integration.
- Clear breadcrumbs before starting a new request.
- Fix a bug in the Celery integration that would drop pending events during worker shutdown (particularly an issue when running with `max_tasks_per_child = 1`)
- Fix a bug with `repr`ing locals whose `__repr__` simultaneously changes the WSGI environment or other data that we're also trying to serialize at the same time.

## 0.7.6

- Fix a bug where artificial frames for Django templates would not be marked as in-app and would always appear as the innermost frame. Implement a heuristic to show template frame closer to `render` or `parse` invocation.

## 0.7.5

- Fix bug into Tornado integration that would send broken cookies to the server.
- Fix a bug in the logging integration that would ignore the client option `with_locals`.

## 0.7.4

- Read release and environment from process environment like the Raven SDK does. The keys are called `SENTRY_RELEASE` and `SENTRY_ENVIRONMENT`.
- Fix a bug in the `serverless` integration where it would not push a new scope for each function call (leaking tags and other things across calls).
- Experimental support for type hints.

## 0.7.3

- Fix crash in AIOHTTP integration when integration was set up but disabled.
- Flask integration now adds usernames, email addresses based on the protocol Flask-User defines on top of Flask-Login.
- New threading integration catches exceptions from crashing threads.
- New method `flush` on hubs and clients. New global `flush` function.
- Add decorator for serverless functions to fix common problems in those environments.
- Fix a bug in the logging integration where using explicit handlers required enabling the integration.

## 0.7.2

- Fix `celery.exceptions.Retry` spamming in Celery integration.

## 0.7.1

- Fix `UnboundLocalError` crash in Celery integration.

## 0.7.0

- Properly display chained exceptions (PEP-3134).
- Rewrite celery integration to monkeypatch instead of using signals due to bugs in Celery 3's signal handling. The Celery scope is also now available in prerun and postrun signals.
- Fix Tornado integration to work with Tornado 6.
- Do not evaluate Django `QuerySet` when trying to capture local variables. Also an internal hook was added to overwrite `repr` for local vars.

## 0.6.9

- Second attempt at fixing the bug that was supposed to be fixed in 0.6.8.

  > No longer access arbitrary sequences in local vars due to possible side effects.

## 0.6.8

- No longer access arbitrary sequences in local vars due to possible side effects.

## 0.6.7

- Sourcecode Django templates is now displayed in stackframes like Jinja templates in Flask already were.
- Updates to AWS Lambda integration for changes Amazon did to their Python 3.7 runtime.
- Fix a bug in the AIOHTTP integration that would report 300s and other HTTP status codes as errors.
- Fix a bug where a crashing `before_send` would crash the SDK and app.
- Fix a bug where cyclic references in e.g. local variables or `extra` data would crash the SDK.

## 0.6.6

- Un-break API of internal `Auth` object that we use in Sentry itself.

## 0.6.5

- Capture WSGI request data eagerly to save memory and avoid issues with uWSGI.
- Ability to use subpaths in DSN.
- Ignore `django.request` logger.

## 0.6.4

- Fix bug that would lead to an `AssertionError: stack must have at least one layer`, at least in testsuites for Flask apps.

## 0.6.3

- New integration for Tornado
- Fix request data in Django, Flask and other WSGI frameworks leaking between events.
- Fix infinite recursion when sending more events in `before_send`.

## 0.6.2

- Fix crash in AWS Lambda integration when using Zappa. This only silences the error, the underlying bug is still in Zappa.

## 0.6.1

- New integration for aiohttp-server.
- Fix crash when reading hostname in broken WSGI environments.

## 0.6.0

- Fix bug where a 429 without Retry-After would not be honored.
- Fix bug where proxy setting would not fall back to `http_proxy` for HTTPs traffic.
- A WSGI middleware is now available for catching errors and adding context about the current request to them.
- Using `logging.debug("test", exc_info=True)` will now attach the current stacktrace if no `sys.exc_info` is available.
- The Python 3.7 runtime for AWS Lambda is now supported.
- Fix a bug that would drop an event or parts of it when it contained bytes that were not UTF-8 encoded.
- Logging an exception will no longer add the exception as breadcrumb to the exception's own event.

## 0.5.5

- New client option `ca_certs`.
- Fix crash with Django and psycopg2.

## 0.5.4

- Fix deprecation warning in relation to the `collections` stdlib module.
- Fix bug that would crash Django and Flask when streaming responses are failing halfway through.

## 0.5.3

- Fix bug where using `push_scope` with a callback would not pop the scope.
- Fix crash when initializing the SDK in `push_scope`.
- Fix bug where IP addresses were sent when `send_default_pii=False`.

## 0.5.2

- Fix bug where events sent through the RQ integration were sometimes lost.
- Remove a deprecation warning about usage of `logger.warn`.
- Fix bug where large frame local variables would lead to the event being rejected by Sentry.

## 0.5.1

- Integration for Redis Queue (RQ)

## 0.5.0

- Fix a bug that would omit several debug logs during SDK initialization.
- Fix issue that sent a event key `""` Sentry wouldn't understand.
- **Breaking change:** The `level` and `event_level` options in the logging integration now work separately from each other.
- Fix a bug in the Sanic integration that would report the exception behind any HTTP error code.
- Fix a bug that would spam breadcrumbs in the Celery integration. Ignore logger `celery.worker.job`.
- Additional attributes on log records are now put into `extra`.
- Integration for Pyramid.
- `sys.argv` is put into extra automatically.

## 0.4.3

- Fix a bug that would leak WSGI responses.

## 0.4.2

- Fix a bug in the Sanic integration that would leak data between requests.
- Fix a bug that would hide all debug logging happening inside of the built-in transport.
- Fix a bug that would report errors for typos in Django's shell.

## 0.4.1

- Fix bug that would only show filenames in stacktraces but not the parent directories.

## 0.4.0

- Changed how integrations are initialized. Integrations are now configured and enabled per-client.

## 0.3.11

- Fix issue with certain deployment tools and the AWS Lambda integration.

## 0.3.10

- Set transactions for Django like in Raven. Which transaction behavior is used can be configured.
- Fix a bug which would omit frame local variables from stacktraces in Celery.
- New option: `attach_stacktrace`

## 0.3.9

- Bugfixes for AWS Lambda integration: Using Zappa did not catch any exceptions.

## 0.3.8

- Nicer log level for internal errors.

## 0.3.7

- Remove `repos` configuration option. There was never a way to make use of this feature.
- Fix a bug in `last_event_id`.
- Add Django SQL queries to breadcrumbs.
- Django integration won't set user attributes if they were already set.
- Report correct SDK version to Sentry.

## 0.3.6

- Integration for Sanic

## 0.3.5

- Integration for AWS Lambda
- Fix mojibake when encoding local variable values

## 0.3.4

- Performance improvement when storing breadcrumbs

## 0.3.3

- Fix crash when breadcrumbs had to be trunchated

## 0.3.2

- Fixed an issue where some paths where not properly sent as absolute paths

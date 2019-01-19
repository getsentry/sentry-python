## 0.6.8

* No longer access arbitrary sequences in local vars due to possible side effects.

## 0.6.7

* Sourcecode Django templates is now displayed in stackframes like Jinja templates in Flask already were.
* Updates to AWS Lambda integration for changes Amazon did to their Python 3.7 runtime.
* Fix a bug in the AIOHTTP integration that would report 300s and other HTTP status codes as errors.
* Fix a bug where a crashing `before_send` would crash the SDK and app.
* Fix a bug where cyclic references in e.g. local variables or `extra` data would crash the SDK.

## 0.6.6

* Un-break API of internal `Auth` object that we use in Sentry itself.

## 0.6.5

* Capture WSGI request data eagerly to save memory and avoid issues with uWSGI.
* Ability to use subpaths in DSN.
* Ignore `django.request` logger.

## 0.6.4

* Fix bug that would lead to an `AssertionError: stack must have at least one layer`, at least in testsuites for Flask apps.

## 0.6.3

* New integration for Tornado
* Fix request data in Django, Flask and other WSGI frameworks leaking between events.
* Fix infinite recursion when sending more events in `before_send`.

## 0.6.2

* Fix crash in AWS Lambda integration when using Zappa. This only silences the error, the underlying bug is still in Zappa.

## 0.6.1

* New integration for aiohttp-server.
* Fix crash when reading hostname in broken WSGI environments.

## 0.6.0

* Fix bug where a 429 without Retry-After would not be honored.
* Fix bug where proxy setting would not fall back to `http_proxy` for HTTPs traffic.
* A WSGI middleware is now available for catching errors and adding context about the current request to them.
* Using `logging.debug("test", exc_info=True)` will now attach the current stacktrace if no `sys.exc_info` is available.
* The Python 3.7 runtime for AWS Lambda is now supported.
* Fix a bug that would drop an event or parts of it when it contained bytes that were not UTF-8 encoded.
* Logging an exception will no longer add the exception as breadcrumb to the exception's own event.

## 0.5.5

* New client option `ca_certs`.
* Fix crash with Django and psycopg2.

## 0.5.4

* Fix deprecation warning in relation to the `collections` stdlib module.
* Fix bug that would crash Django and Flask when streaming responses are failing halfway through.

## 0.5.3

* Fix bug where using `push_scope` with a callback would not pop the scope.
* Fix crash when initializing the SDK in `push_scope`.
* Fix bug where IP addresses were sent when `send_default_pii=False`.

## 0.5.2

* Fix bug where events sent through the RQ integration were sometimes lost.
* Remove a deprecation warning about usage of `logger.warn`.
* Fix bug where large frame local variables would lead to the event being rejected by Sentry.

## 0.5.1

* Integration for Redis Queue (RQ)

## 0.5.0

* Fix a bug that would omit several debug logs during SDK initialization.
* Fix issue that sent a event key `""` Sentry wouldn't understand.
* **Breaking change:** The `level` and `event_level` options in the logging integration now work separately from each other.
* Fix a bug in the Sanic integration that would report the exception behind any HTTP error code.
* Fix a bug that would spam breadcrumbs in the Celery integration. Ignore logger `celery.worker.job`.
* Additional attributes on log records are now put into `extra`.
* Integration for Pyramid.
* `sys.argv` is put into extra automatically.

## 0.4.3

* Fix a bug that would leak WSGI responses.

## 0.4.2

* Fix a bug in the Sanic integration that would leak data between requests.
* Fix a bug that would hide all debug logging happening inside of the built-in transport.
* Fix a bug that would report errors for typos in Django's shell.

## 0.4.1

* Fix bug that would only show filenames in stacktraces but not the parent
  directories.

## 0.4.0

* Changed how integrations are initialized. Integrations are now
  configured and enabled per-client.

## 0.3.11

* Fix issue with certain deployment tools and the AWS Lambda integration.

## 0.3.10

* Set transactions for Django like in Raven. Which transaction behavior is used
  can be configured.
* Fix a bug which would omit frame local variables from stacktraces in Celery.
* New option: `attach_stacktrace`

## 0.3.9

* Bugfixes for AWS Lambda integration: Using Zappa did not catch any exceptions.

## 0.3.8

* Nicer log level for internal errors.

## 0.3.7

* Remove `repos` configuration option. There was never a way to make use of
  this feature.
* Fix a bug in `last_event_id`.
* Add Django SQL queries to breadcrumbs.
* Django integration won't set user attributes if they were already set.
* Report correct SDK version to Sentry.

## 0.3.6

* Integration for Sanic

## 0.3.5

* Integration for AWS Lambda
* Fix mojibake when encoding local variable values

## 0.3.4

* Performance improvement when storing breadcrumbs

## 0.3.3

* Fix crash when breadcrumbs had to be trunchated

## 0.3.2

* Fixed an issue where some paths where not properly sent as absolute paths

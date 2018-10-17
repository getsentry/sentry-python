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

# Using Sentry with stdlib logging

Calling ``sentry_sdk.init()`` already captures any logging message with a level
higher than or equal to ``INFO``. You can change this behavior by explicitly
passing the logging integration like any other:

    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk import init

    sentry_logging = LoggingIntegration(
        level=logging.DEBUG,
        event_level=logging.ERROR
    )
    init(dsn="https://foo@sentry.io/123", integrations=[sentry_logging])

The above configuration captures *all* messages, now including ``DEBUG``, as
breadcrumbs. Messages with level ``ERROR`` and above will show up as their own
events as well.

## Config

* ``level`` (default ``INFO``): Log records with a level higher than or equal
  to ``level`` will be recorded as breadcrumbs. Any log record with a level
  lower than this one is completely ignored.

* ``event_level`` (default ``None``): Log records with a level higher than or
  equal to ``event_level`` will additionally be reported as event. A value of
  ``None`` means that no log records will be sent as events.

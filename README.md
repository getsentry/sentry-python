<p align="center">
    <a href="https://sentry.io" target="_blank" align="center">
        <img src="https://sentry-brand.storage.googleapis.com/sentry-logo-black.png" width="280">
    </a>
<br/>
    <h1>sentry-python - Sentry SDK for Python</h1>
</p>

Experimental Python SDK

[![Build Status](https://travis-ci.com/getsentry/sentry-python.svg?branch=master)](https://travis-ci.com/getsentry/sentry-python)

***Sentry-Python is an experimental SDK for Sentry.*** For a stable one, use
[raven](https://github.com/getsentry/raven-python).

# Getting started with the new Sentry SDK for Python

Install this package with ``pip install sentry-sdk``. Then, in your code:

    import sentry_sdk
    sentry_sdk.init(dsn="https://foo@sentry.io/123")

After initialization, you can capture exceptions like this:

    sentry_sdk.capture_exception(ValueError())

    try:
        raise ValueError()
    except Exception:
        sentry_sdk.capture_exception()

...or send messages:

    sentry_sdk.capture_message("Hi Sentry!")

## Scopes (contexts, tags)

You can create a scope to attach data to all events happening inside of it:

    with sentry_sdk.get_current_hub().push_scope():
        with sentry_sdk.configure_scope() as scope:
            scope.transaction = "my_view_name"
            scope.set_tag("key", "value")
            scope.user = {"id": 123}

        # ValueError event will have all that data attached
        capture_exception(ValueError())

    # This one not since it is outside of the context manager
    capture_exception(ValueError())

Scopes can be nested. If you call ``push_scope`` inside of the
``with``-statement again, that scope will be pushed onto a stack. It will also
inherit all data from the outer scope.

### Breadcrumbs

Breadcrumbs also live on the stack. By default any (non-debug) log message
anywhere in your system ends up as a breadcrumb, see [the logging
docs](./docs/logging.md) for more information. You can, however, also create
breadcrumbs manually:

    sentry_sdk.add_breadcrumb({
        # "ty": "log",
        # "level": "debug",
        # "category": "myapp.models",
        "message": "hi"
    })

## Integrations

Head over to [the other pages](./docs/) to check out integrations, which use
these low-level APIs so you don't have to.

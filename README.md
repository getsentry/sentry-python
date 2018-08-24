<p align="center">
    <a href="https://sentry.io" target="_blank" align="center">
        <img src="https://sentry-brand.storage.googleapis.com/sentry-logo-black.png" width="280">
    </a>
</p>

# sentry-python - Sentry SDK for Python

[![Build Status](https://travis-ci.com/getsentry/sentry-python.svg?branch=master)](https://travis-ci.com/getsentry/sentry-python)

***Sentry-Python is an experimental SDK for Sentry.*** For a stable one, use
[raven](https://github.com/getsentry/raven-python).

## Getting started with the new Sentry SDK for Python

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

### Scopes in unconfigured environments

If you never call ``init``, no data will ever get sent to any server. In such
situations, code like this is essentially deadweight:

    with sentry_sdk.configure_scope() as scope:
        scope.user = _get_user_data()

Sentry-Python supports an alternative syntax for configuring a scope that
solves this problem:

    @sentry_sdk.configure_scope
    def _(scope):
        scope.user = _get_user_data()

Your function will just not be executed if there is no client configured.

In your testing and development environment you still might want to run that
code without sending any events. In that case, simply call ``init`` without a
DSN:

    sentry_sdk.init()

### Breadcrumbs

Breadcrumbs also live on the stack. By default any (non-debug) log message
anywhere in your system ends up as a breadcrumb, see [the logging
docs](./docs/logging.md) for more information. You can, however, also create
breadcrumbs manually:

    sentry_sdk.add_breadcrumb(
        timestamp=datetime.datetime.now(),
        type="log",
        level="debug",
        # message="hi",
        # category="myapp.models",
    })

You can also pass a callback to `add_breadcrumb` like so:

    sentry_sdk.add_breadcrumb(lambda: {
        "timestamp": datetime.datetime.now(),
        "type": "log",
        "level": "debug",
        # "message": "hi",
        # "category": "myapp.models",
    })

The callback will only be called if a sentry client is configured.


## Concurrency

* Sentry-Python currently does not support gevent-based setups.
* On ``init``, Sentry-Python spawns a thread on its own. That means if you use
  ``uwsgi``, you currently need to enable threads.
* On Python 3.7, Sentry-Python supports and automatically uses ``ContextVars``.
  This should effectively enable Sentry-Python to work with ``asyncio`` under
  that Python version.


## PII

Currently Sentry-Python does not send any personally-identifiable user data
with events by default. You need to explicitly enable this behavior with the
``send_default_pii`` option passed to ``init``:

    init(..., send_default_pii=True)

## Integrations

Head over to [the other pages](https://github.com/getsentry/sentry-python/tree/master/docs)
to check out integrations, which use these low-level APIs so you don't have to.

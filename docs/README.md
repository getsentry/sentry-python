# Getting started with the Sentry SDK for Python

    import sentry_sdk
    sentry_sdk.init(dsn="https://foo@sentry.io/123")

After initialization, you can capture exceptions like this:

    sentry_sdk.capture_exception(ValueError())

    try:
        raise ValueError()
    except Exception:
        sentry_sdk.capture_exception()

You can create a scope to prepare data to attach to an event:

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

Head over to the other files to check out integrations, which use these
low-level APIs so you don't have to.

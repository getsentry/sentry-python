=============
Top Level API
=============

This is the user facing API of the SDK. It's exposed as ``sentry_sdk``.
With this API you can implement a custom performance monitoring or error reporting solution.

Initializing the SDK
====================

.. autoclass:: sentry_sdk.client.ClientConstructor
   :members:
   :undoc-members:
   :special-members: __init__
   :noindex:

Capturing Data
==============

.. autofunction:: sentry_sdk.api.capture_event
.. autofunction:: sentry_sdk.api.capture_exception
.. autofunction:: sentry_sdk.api.capture_message


Enriching Events
================

.. autofunction:: sentry_sdk.api.add_attachment
.. autofunction:: sentry_sdk.api.add_breadcrumb
.. autofunction:: sentry_sdk.api.set_context
.. autofunction:: sentry_sdk.api.set_extra
.. autofunction:: sentry_sdk.api.set_level
.. autofunction:: sentry_sdk.api.set_tag
.. autofunction:: sentry_sdk.api.set_user


Performance Monitoring
======================

.. autofunction:: sentry_sdk.api.trace
.. autofunction:: sentry_sdk.api.continue_trace
.. autofunction:: sentry_sdk.api.get_current_span
.. autofunction:: sentry_sdk.api.start_span
.. autofunction:: sentry_sdk.api.start_transaction
.. autofunction:: sentry_sdk.api.update_current_span


Distributed Tracing
===================

.. autofunction:: sentry_sdk.api.get_baggage
.. autofunction:: sentry_sdk.api.get_traceparent


Client Management
=================

.. autofunction:: sentry_sdk.api.is_initialized
.. autofunction:: sentry_sdk.api.get_client


Managing Scope (advanced)
=========================

.. autofunction:: sentry_sdk.api.configure_scope
.. autofunction:: sentry_sdk.api.push_scope

.. autofunction:: sentry_sdk.api.new_scope


Session Tracking
================

Session tracking counts how many users are actively using your application, so
that Sentry can show crash-free rates. When you use one of the web framework
integrations (such as the WSGI, ASGI, or Django integrations), a session is
started and finished automatically for each incoming request.

For projects that are not served over WSGI/ASGI -- command line tools,
background workers, daemons, and so on -- there is no automatic session
tracking, but you can track sessions manually around a unit of work.

The simplest way is the :py:func:`sentry_sdk.sessions.track_session` context
manager, which starts a session on the given scope when entering and finishes
it when exiting. It is a no-op if session tracking is not enabled, so you can
use it unconditionally::

    import sentry_sdk
    from sentry_sdk.scope import isolation_scope
    from sentry_sdk.sessions import track_session

    sentry_sdk.init(
        dsn="___DSN___",
        # On by default; kept explicit here for clarity
        auto_session_tracking=True,
    )

    # For example, wrap a single unit of work of a background worker.
    with isolation_scope() as scope:
        with track_session(scope, session_mode="application"):
            process_job()

If you prefer explicit control, you can start and stop sessions with
:py:func:`sentry_sdk.api.start_session` and :py:func:`sentry_sdk.api.end_session`
instead::

    import sentry_sdk

    sentry_sdk.init(
        dsn="___DSN___",
        auto_session_tracking=True,
    )

    sentry_sdk.start_session(session_mode="application")
    try:
        process_job()
    finally:
        sentry_sdk.end_session()

.. autofunction:: sentry_sdk.sessions.track_session
.. autofunction:: sentry_sdk.api.start_session
.. autofunction:: sentry_sdk.api.end_session

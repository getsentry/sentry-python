=============
Top Level API
=============

This is the user facing API of the SDK. It's exposed as ``sentry_sdk``.
With this API you can implement a custom performance monitoring or error reporting solution.


Capturing Data
==============

.. autofunction:: sentry_sdk.api.capture_event
.. autofunction:: sentry_sdk.api.capture_exception
.. autofunction:: sentry_sdk.api.capture_message


Enriching Events
================

.. autofunction:: sentry_sdk.api.add_breadcrumb
.. autofunction:: sentry_sdk.api.set_context
.. autofunction:: sentry_sdk.api.set_extra
.. autofunction:: sentry_sdk.api.set_level
.. autofunction:: sentry_sdk.api.set_tag
.. autofunction:: sentry_sdk.api.set_user


Performance Monitoring
======================

.. autofunction:: sentry_sdk.api.continue_trace
.. autofunction:: sentry_sdk.api.get_current_span
.. autofunction:: sentry_sdk.api.start_span
.. autofunction:: sentry_sdk.api.start_transaction


Distributed Tracing
===================

.. autofunction:: sentry_sdk.api.get_baggage
.. autofunction:: sentry_sdk.api.get_traceparent


Managing Scope (advanced)
=========================

.. autofunction:: sentry_sdk.api.configure_scope
.. autofunction:: sentry_sdk.api.push_scope


.. Not documented (On purpose. Not sure if anyone should use those)
.. last_event_id()
.. flush()

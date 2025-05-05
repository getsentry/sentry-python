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

.. autofunction:: sentry_sdk.api.continue_trace
.. autofunction:: sentry_sdk.api.get_current_span
.. autofunction:: sentry_sdk.api.start_span
.. autofunction:: sentry_sdk.api.start_transaction


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

.. autofunction:: sentry_sdk.api.new_scope

=======
Tracing
=======

With Performance Monitoring, Sentry tracks your software's performance, measuring variables such as throughput and latency.

Manual Instrumentation
=====================

You can manually start transactions and spans to trace custom operations in your application.

Transactions
------------
A transaction represents a single instance of a service being called. It forms the root of a trace tree.

.. code-block:: python

    import sentry_sdk

    # Start a transaction as a context manager
    with sentry_sdk.start_transaction(name="process-order"):
        # Your application logic here
        pass

Spans
-----
Spans represent individual units of work within a transaction, such as a database query or an API call.

.. code-block:: python

    import sentry_sdk

    # Start a child span under the current transaction
    with sentry_sdk.start_span(op="db.query", name="SELECT * FROM users"):
        # Your operation here
        pass


Managing Context with Scopes
============================

Sentry use **Scopes** to manage execution context and event enrichment. In SDK 2.x, top-level APIs replace the deprecated Hub model.

Isolation Scope
---------------
The `isolation_scope` should be used for isolating data that belongs to a single request or job lifecycle. It propagates data across child scopes.

.. code-block:: python

    import sentry_sdk

    with sentry_sdk.isolation_scope() as scope:
        scope.set_tag("user_type", "admin")
        # Operations triggered here will include the tag

New Scope
---------
The `new_scope` forks the current scope for local, short-lived modifications. 

.. code-block:: python

    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        scope.set_extra("temp_debug_data", 123)
        # Changes are discarded when existing the block

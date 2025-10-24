Mark expected N+1 loops
======================

The SDK provides a small helper to mark transactions or spans where an N+1 loop is
expected and acceptable.

.. code-block:: python

    from sentry_sdk.performance import allow_n_plus_one

    with sentry_sdk.start_transaction(name="process_items"):
        with allow_n_plus_one("expected batch processing"):
            for item in items:
                process(item)

Notes
-----

- This helper sets the tag ``sentry.n_plus_one.ignore`` (and optional
  ``sentry.n_plus_one.reason``) on the current transaction and current span.
- Server-side support is required for the N+1 detector to actually ignore
  transactions with this tag. The SDK only attaches the metadata.

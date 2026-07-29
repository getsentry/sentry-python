import warnings

import sentry_sdk
import sentry_sdk.tracing


def test_no_warnings_scope_to_transaction_finish():
    transaction = sentry_sdk.tracing.Transaction()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        transaction.finish(sentry_sdk.Scope())

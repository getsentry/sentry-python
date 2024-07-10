import warnings

import pytest

import sentry_sdk
import sentry_sdk.tracing


@pytest.mark.parametrize("parameter_value", (sentry_sdk.Hub(), sentry_sdk.Scope()))
def test_passing_hub_parameter_to_transaction_finish(parameter_value):
    transaction = sentry_sdk.tracing.Transaction()
    with pytest.warns(DeprecationWarning):
        transaction.finish(hub=parameter_value)


def test_passing_hub_object_to_scope_transaction_finish():
    transaction = sentry_sdk.tracing.Transaction()
    with pytest.warns(DeprecationWarning):
        transaction.finish(sentry_sdk.Hub())


def test_no_warnings_scope_to_transaction_finish():
    transaction = sentry_sdk.tracing.Transaction()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        transaction.finish(sentry_sdk.Scope())

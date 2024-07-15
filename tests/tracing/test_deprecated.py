import warnings

import pytest

import sentry_sdk
import sentry_sdk.tracing
from sentry_sdk import start_span

from sentry_sdk.tracing import Span


@pytest.mark.skip(reason="This deprecated feature has been removed in SDK 2.0.")
def test_start_span_to_start_transaction(sentry_init, capture_events):
    # XXX: this only exists for backwards compatibility with code before
    # Transaction / start_transaction were introduced.
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(transaction="/1/"):
        pass

    with start_span(Span(transaction="/2/")):
        pass

    assert len(events) == 2
    assert events[0]["transaction"] == "/1/"
    assert events[1]["transaction"] == "/2/"


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

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


@pytest.mark.parametrize(
    "parameter_value_getter",
    # Use lambda to avoid Hub deprecation warning here (will suppress it in the test)
    (lambda: sentry_sdk.Hub(), lambda: sentry_sdk.Scope()),
)
def test_passing_hub_parameter_to_transaction_finish(
    suppress_deprecation_warnings, parameter_value_getter
):
    parameter_value = parameter_value_getter()
    transaction = sentry_sdk.tracing.Transaction()
    with pytest.warns(DeprecationWarning):
        transaction.finish(hub=parameter_value)


def test_passing_hub_object_to_scope_transaction_finish(suppress_deprecation_warnings):
    transaction = sentry_sdk.tracing.Transaction()

    # Do not move the following line under the `with` statement. Otherwise, the Hub.__init__ deprecation
    # warning will be confused with the transaction.finish deprecation warning that we are testing.
    hub = sentry_sdk.Hub()

    with pytest.warns(DeprecationWarning):
        transaction.finish(hub)


def test_warning_scope_to_transaction_finish():
    transaction = sentry_sdk.tracing.Transaction()
    with pytest.warns(DeprecationWarning):
        transaction.finish(sentry_sdk.Scope())

import pytest

import sentry_sdk
from sentry_sdk.tracing_utils import Baggage

TEST_TRACE_ID_SAMPLE_RANDS = {
    "00000000000000000000000000000000": 0.8766381713144122,
    "01234567012345670123456701234567": 0.6451742521664413,
    "0123456789abcdef0123456789abcdef": 0.9338861957669223,
}
"""
A dictionary of some trace IDs used in the tests, and their precomputed sample_rand values.

sample_rand values are pseudo-random numbers, deterministically generated from the trace ID.
"""


@pytest.mark.parametrize(
    ("trace_id", "expected_sample_rand"),
    TEST_TRACE_ID_SAMPLE_RANDS.items(),
)
# test 21 linearly spaced sample_rate values from 0.0 to 1.0, inclusive
@pytest.mark.parametrize("sample_rate", (i / 20 for i in range(21)))
def test_deterministic_sampled(
    sentry_init, capture_events, sample_rate, trace_id, expected_sample_rand
):
    """
    Test that the sample_rand value is deterministic based on the trace ID, and
    that it is used to determine the sampling decision. Also, ensure that the
    transaction's baggage contains the sample_rand value.
    """
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with sentry_sdk.start_transaction(trace_id=trace_id) as transaction:
        assert transaction.get_baggage().sentry_items["sample_rand"] == str(
            expected_sample_rand
        )

    # Transaction event captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(events) == int(expected_sample_rand < sample_rate)


@pytest.mark.parametrize("sample_rand", (0.0, 0.2, 0.4, 0.6, 0.8))
@pytest.mark.parametrize("sample_rate", (0.0, 0.2, 0.4, 0.6, 0.8, 1.0))
def test_transaction_uses_incoming_sample_rand(
    sentry_init, capture_events, sample_rate, sample_rand
):
    """
    Test that the transaction uses the sample_rand value from the incoming baggage.
    """
    baggage = Baggage(sentry_items={"sample_rand": str(sample_rand)})

    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with sentry_sdk.start_transaction(baggage=baggage) as transaction:
        assert transaction.get_baggage().sentry_items["sample_rand"] == str(sample_rand)

    # Transaction event captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(events) == int(sample_rand < sample_rate)

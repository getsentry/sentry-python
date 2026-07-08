from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.tracing_utils import Baggage


@pytest.mark.parametrize("sample_rand", (0.0, 0.25, 0.5, 0.75))
@pytest.mark.parametrize("sample_rate", (0.0, 0.25, 0.5, 0.75, 1.0))
def test_deterministic_sampled(sentry_init, capture_events, sample_rate, sample_rand):
    """
    Test that sample_rand is generated on new traces, that it is used to
    make the sampling decision, and that it is included in the transaction's
    baggage.
    """
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange",
        return_value=int(sample_rand * 1000000),
    ):
        with sentry_sdk.start_transaction() as transaction:
            assert (
                transaction.get_baggage().sentry_items["sample_rand"]
                == f"{sample_rand:.6f}"  # noqa: E231
            )

    # Transaction event captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(events) == int(sample_rand < sample_rate)


@pytest.mark.parametrize("sample_rand", (0.0, 0.25, 0.5, 0.75))
@pytest.mark.parametrize("sample_rate", (0.0, 0.25, 0.5, 0.75, 1.0))
def test_deterministic_sampled_span_streaming(
    sentry_init, capture_items, sample_rate, sample_rand
):
    """
    Test that sample_rand is generated on new traces, that it is used to
    make the sampling decision, and that it is included in the segment's
    baggage.
    """
    sentry_init(
        traces_sample_rate=sample_rate,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items("span")

    with mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange",
        return_value=int(sample_rand * 1000000),
    ):
        with sentry_sdk.traces.start_span(name="span") as span:
            assert (
                span._get_baggage().sentry_items["sample_rand"] == f"{sample_rand:.6f}"  # noqa: E231
            )

    # Span captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(items) == int(sample_rand < sample_rate)


@pytest.mark.parametrize("sample_rand", (0.0, 0.25, 0.5, 0.75))
@pytest.mark.parametrize("sample_rate", (0.0, 0.25, 0.5, 0.75, 1.0))
def test_transaction_uses_incoming_sample_rand(
    sentry_init, capture_events, sample_rate, sample_rand
):
    """
    Test that the transaction uses the sample_rand value from the incoming baggage.
    """
    baggage = Baggage(sentry_items={"sample_rand": f"{sample_rand:.6f}"})  # noqa: E231

    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with sentry_sdk.start_transaction(baggage=baggage) as transaction:
        assert (
            transaction.get_baggage().sentry_items["sample_rand"]
            == f"{sample_rand:.6f}"  # noqa: E231
        )

    # Transaction event captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(events) == int(sample_rand < sample_rate)


@pytest.mark.parametrize("sample_rand", (0.0, 0.25, 0.5, 0.75))
@pytest.mark.parametrize("sample_rate", (0.0, 0.25, 0.5, 0.75, 1.0))
def test_transaction_uses_incoming_sample_rand_span_streaming(
    sentry_init, capture_items, sample_rate, sample_rand
):
    """
    Test that the segment uses the sample_rand value from the incoming baggage.
    """
    sentry_init(
        traces_sample_rate=sample_rate,
        _experiments={"trace_lifecycle": "stream"},
    )
    items = capture_items()

    sentry_sdk.traces.continue_trace(
        {"baggage": f"sentry-sample_rand={sample_rand:.6f}"}
    )

    with sentry_sdk.traces.start_span(name="span") as span:
        assert (
            span._get_baggage().sentry_items["sample_rand"] == f"{sample_rand:.6f}"  # noqa: E231
        )

    # Span captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(items) == int(sample_rand < sample_rate)

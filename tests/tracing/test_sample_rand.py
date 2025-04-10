import decimal
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, SENTRY_TRACE_HEADER_NAME


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
        "sentry_sdk.tracing_utils.Random.uniform", return_value=sample_rand
    ):
        with sentry_sdk.start_span() as root_span:
            assert (
                root_span.get_baggage().sentry_items["sample_rand"]
                == f"{sample_rand:.6f}"  # noqa: E231
            )

    # Transaction event captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(events) == int(sample_rand < sample_rate)


@pytest.mark.parametrize("sample_rand", (0.0, 0.25, 0.5, 0.75))
@pytest.mark.parametrize("sample_rate", (0.0, 0.25, 0.5, 0.75, 1.0))
def test_transaction_uses_incoming_sample_rand(
    sentry_init, capture_events, sample_rate, sample_rand
):
    """
    Test that the transaction uses the sample_rand value from the incoming baggage.
    """
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    baggage = f"sentry-sample_rand={sample_rand:.6f},sentry-trace_id=771a43a4192642f0b136d5159a501700"  # noqa: E231
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef"

    with sentry_sdk.continue_trace(
        {BAGGAGE_HEADER_NAME: baggage, SENTRY_TRACE_HEADER_NAME: sentry_trace}
    ):
        with sentry_sdk.start_span() as root_span:
            assert (
                root_span.get_baggage().sentry_items["sample_rand"]
                == f"{sample_rand:.6f}"  # noqa: E231
            )

    # Transaction event captured if sample_rand < sample_rate, indicating that
    # sample_rand is used to make the sampling decision.
    assert len(events) == int(sample_rand < sample_rate)


def test_decimal_context(sentry_init, capture_events):
    """
    Ensure that having a decimal context with a precision below 6
    does not cause an InvalidOperation exception.
    """
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    old_prec = decimal.getcontext().prec
    decimal.getcontext().prec = 2

    try:
        with mock.patch(
            "sentry_sdk.tracing_utils.Random.uniform", return_value=0.123456789
        ):
            with sentry_sdk.start_span() as root_span:
                assert root_span.get_baggage().sentry_items["sample_rand"] == "0.123456"
    finally:
        decimal.getcontext().prec = old_prec

    assert len(events) == 1


@pytest.mark.parametrize(
    "incoming_sample_rand,expected_sample_rand",
    (
        ("0.0100015", "0.0100015"),
        ("0.1", "0.1"),
    ),
)
def test_unexpected_incoming_sample_rand_precision(
    sentry_init, capture_events, incoming_sample_rand, expected_sample_rand
):
    """
    Test that incoming sample_rand is correctly interpreted even if it looks unexpected.

    We shouldn't be getting arbitrary precision sample_rand in incoming headers,
    but if we do for some reason, check that we don't tamper with it.
    """
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    baggage = f"sentry-sample_rand={incoming_sample_rand},sentry-trace_id=771a43a4192642f0b136d5159a501700"  # noqa: E231
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef"

    with sentry_sdk.continue_trace(
        {BAGGAGE_HEADER_NAME: baggage, SENTRY_TRACE_HEADER_NAME: sentry_trace}
    ):
        with sentry_sdk.start_span() as root_span:
            assert (
                root_span.get_baggage().sentry_items["sample_rand"]
                == expected_sample_rand
            )

    assert len(events) == 1


@pytest.mark.parametrize(
    "incoming_sample_rand",
    ("abc", "null", "47"),
)
def test_invalid_incoming_sample_rand(sentry_init, incoming_sample_rand):
    """Test that we handle malformed incoming sample_rand."""
    sentry_init(traces_sample_rate=1.0)

    baggage = f"sentry-sample_rand={incoming_sample_rand},sentry-trace_id=771a43a4192642f0b136d5159a501700"  # noqa: E231
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef"

    with sentry_sdk.continue_trace(
        {BAGGAGE_HEADER_NAME: baggage, SENTRY_TRACE_HEADER_NAME: sentry_trace}
    ):
        with sentry_sdk.start_span():
            pass

    # The behavior here is undefined since we got a broken incoming trace,
    # so as long as the SDK doesn't produce an error we consider this
    # testcase a success.


@pytest.mark.parametrize("incoming", ((0.0, "true"), (1.0, "false")))
def test_invalid_incoming_sampled_and_sample_rate(sentry_init, incoming):
    """
    Test that we don't error out in case we can't generate a sample_rand that
    would respect the incoming sampled and sample_rate.
    """
    sentry_init(traces_sample_rate=1.0)

    sample_rate, sampled = incoming

    baggage = (
        f"sentry-sample_rate={sample_rate},"  # noqa: E231
        f"sentry-sampled={sampled},"  # noqa: E231
        "sentry-trace_id=771a43a4192642f0b136d5159a501700"
    )
    sentry_trace = f"771a43a4192642f0b136d5159a501700-1234567890abcdef-{1 if sampled == 'true' else 0}"

    with sentry_sdk.continue_trace(
        {BAGGAGE_HEADER_NAME: baggage, SENTRY_TRACE_HEADER_NAME: sentry_trace}
    ):
        with sentry_sdk.start_span():
            pass

    # The behavior here is undefined since we got a broken incoming trace,
    # so as long as the SDK doesn't produce an error we consider this
    # testcase a success.

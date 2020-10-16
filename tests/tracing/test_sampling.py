import pytest

from sentry_sdk import start_span, start_transaction
from sentry_sdk.tracing import _is_valid_sample_rate
from sentry_sdk.utils import logger

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def test_sampling_decided_only_for_transactions(sentry_init, capture_events):
    sentry_init(traces_sample_rate=0.5)

    with start_transaction(name="hi") as transaction:
        assert transaction.sampled is not None

        with start_span() as span:
            assert span.sampled == transaction.sampled

    with start_span() as span:
        assert span.sampled is None


def test_nested_transaction_sampling_override():
    with start_transaction(name="outer", sampled=True) as outer_transaction:
        assert outer_transaction.sampled is True
        with start_transaction(name="inner", sampled=False) as inner_transaction:
            assert inner_transaction.sampled is False
        assert outer_transaction.sampled is True


def test_no_double_sampling(sentry_init, capture_events):
    # Transactions should not be subject to the global/error sample rate.
    # Only the traces_sample_rate should apply.
    sentry_init(traces_sample_rate=1.0, sample_rate=0.0)
    events = capture_events()

    with start_transaction(name="/"):
        pass

    assert len(events) == 1


@pytest.mark.parametrize(
    "rate",
    [0.0, 0.1231, 1.0, True, False],
)
def test_accepts_valid_sample_rate(rate):
    with mock.patch.object(logger, "warning", mock.Mock()):
        result = _is_valid_sample_rate(rate)
        assert logger.warning.called is False
        assert result is True


@pytest.mark.parametrize(
    "rate",
    [
        "dogs are great",  # wrong type
        (0, 1),  # wrong type
        {"Maisey": "Charllie"},  # wrong type
        [True, True],  # wrong type
        {0.2012},  # wrong type
        float("NaN"),  # wrong type
        None,  # wrong type
        -1.121,  # wrong value
        1.231,  # wrong value
    ],
)
def test_warns_on_invalid_sample_rate(rate, StringContaining):  # noqa: N803
    with mock.patch.object(logger, "warning", mock.Mock()):
        result = _is_valid_sample_rate(rate)
        logger.warning.assert_any_call(StringContaining("Given sample rate is invalid"))
        assert result is False

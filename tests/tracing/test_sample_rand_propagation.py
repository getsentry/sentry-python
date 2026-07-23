"""
These tests exist to verify that Scope.continue_trace() correctly propagates the
sample_rand value onto the transaction's baggage.

We check both the case where there is an incoming sample_rand, as well as the case
where we need to compute it because it is missing.
"""

from unittest import mock
from unittest.mock import Mock

import sentry_sdk


def test_continue_trace_with_sample_rand():
    """
    Test that an incoming sample_rand is propagated onto the transaction's baggage.
    """
    headers = {
        "sentry-trace": "00000000000000000000000000000000-0000000000000000-0",
        "baggage": "sentry-sample_rand=0.1,sentry-sample_rate=0.5",
    }

    transaction = sentry_sdk.continue_trace(headers)
    assert transaction.get_baggage().sentry_items["sample_rand"] == "0.1"


def test_continue_trace_with_sample_rand_span_streaming(sentry_init):
    """
    Test that an incoming sample_rand is propagated onto the segment's baggage.
    """
    sentry_init(traces_sample_rate=1.0, trace_lifecycle="stream")

    headers = {
        "sentry-trace": "00000000000000000000000000000000-0000000000000000-1",
        "baggage": "sentry-sample_rand=0.100000,sentry-sample_rate=0.5",
    }

    sentry_sdk.traces.continue_trace(headers)
    with sentry_sdk.traces.start_span(name="span") as segment:
        assert segment._get_baggage().sentry_items["sample_rand"] == "0.100000"


def test_continue_trace_missing_sample_rand():
    """
    Test that a missing sample_rand is filled in onto the transaction's baggage.
    """

    headers = {
        "sentry-trace": "00000000000000000000000000000000-0000000000000000",
        "baggage": "sentry-placeholder=asdf",
    }

    with mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange", Mock(return_value=500000)
    ):
        transaction = sentry_sdk.continue_trace(headers)

    assert transaction.get_baggage().sentry_items["sample_rand"] == "0.500000"


def test_continue_trace_missing_sample_rand_span_streaming(sentry_init):
    """
    Test that a missing sample_rand is filled in onto the segment's baggage.
    """

    sentry_init(traces_sample_rate=1.0, trace_lifecycle="stream")

    headers = {
        "sentry-trace": "00000000000000000000000000000000-0000000000000000",
        "baggage": "sentry-placeholder=asdf",
    }

    with mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange", Mock(return_value=500000)
    ):
        sentry_sdk.traces.continue_trace(headers)
        with sentry_sdk.traces.start_span(name="span") as span:
            assert span._get_baggage().sentry_items["sample_rand"] == "0.500000"

"""
These tests exist to verify that Scope.continue_trace() correctly propagates the
sample_rand value onto the transaction's baggage.

We check both the case where there is an incoming sample_rand, as well as the case
where we need to compute it because it is missing.
"""

from unittest import mock

import sentry_sdk


def test_continue_trace_with_sample_rand(sentry_init):
    """
    Test that an incoming sample_rand is propagated onto the transaction's baggage.
    """
    sentry_init()

    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-0",
        "baggage": "sentry-sample_rand=0.1,sentry-sample_rate=0.5",
    }

    with sentry_sdk.continue_trace(headers):
        with sentry_sdk.start_span(name="root-span") as root_span:
            assert root_span.get_baggage().sentry_items["sample_rand"] == "0.1"


def test_continue_trace_missing_sample_rand(sentry_init):
    """
    Test that a missing sample_rand is filled in onto the transaction's baggage.
    """
    sentry_init()

    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef",
        "baggage": "sentry-placeholder=asdf",
    }

    with mock.patch("sentry_sdk.tracing_utils.Random.uniform", return_value=0.5):
        with sentry_sdk.continue_trace(headers):
            with sentry_sdk.start_span(name="root-span") as root_span:
                assert root_span.get_baggage().sentry_items["sample_rand"] == "0.500000"

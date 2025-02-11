"""
These tests exist to verify that Scope.continue_trace() correctly propagates the
sample_rand value onto the transaction's baggage.

We check both the case where there is an incoming sample_rand, as well as the case
where we need to compute it because it is missing.
"""

import pytest

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


@pytest.mark.parametrize(
    ("parent_sampled", "sample_rate", "expected_sample_rand"),
    (
        (None, None, "0.8766381713144122"),
        (None, "0.5", "0.8766381713144122"),
        (False, None, "0.8766381713144122"),
        (True, None, "0.8766381713144122"),
        (False, "0.0", "0.8766381713144122"),
        (False, "0.01", "0.8778717896012681"),
        (True, "0.01", "0.008766381713144122"),
        (False, "0.1", "0.888974354182971"),
        (True, "0.1", "0.08766381713144122"),
        (False, "0.5", "0.9383190856572061"),
        (True, "0.5", "0.4383190856572061"),
        (True, "1.0", "0.8766381713144122"),
    ),
)
def test_continue_trace_missing_sample_rand(
    parent_sampled, sample_rate, expected_sample_rand
):
    """
    Test that a missing sample_rand is filled in onto the transaction's baggage. The sample_rand
    is pseudorandomly generated based on the trace_id, so we assert the exact values that should
    be generated.
    """
    headers = {
        "sentry-trace": f"00000000000000000000000000000000-0000000000000000{sampled_flag(parent_sampled)}",
        "baggage": f"sentry-sample_rate={sample_rate}",
    }

    transaction = sentry_sdk.continue_trace(headers)
    assert transaction.get_baggage().sentry_items["sample_rand"] == expected_sample_rand


def sampled_flag(sampled):
    """
    convenience function to get the sampled flag on the sentry-trace header, given a parent
    sampling decision.
    """
    if sampled is None:
        return ""
    elif sampled is True:
        return "-1"
    else:
        return "-0"

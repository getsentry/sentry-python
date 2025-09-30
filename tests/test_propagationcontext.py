from unittest import mock
from unittest.mock import Mock

import pytest

from sentry_sdk.tracing_utils import PropagationContext


SAMPLED_FLAG = {
    None: "",
    False: "-0",
    True: "-1",
}
"""Maps the `sampled` value to the flag appended to the sentry-trace header."""


def test_empty_context():
    ctx = PropagationContext()

    assert ctx.trace_id is not None
    assert len(ctx.trace_id) == 32

    assert ctx.span_id is not None
    assert len(ctx.span_id) == 16

    assert ctx.parent_span_id is None
    assert ctx.parent_sampled is None
    assert ctx.dynamic_sampling_context is None


def test_context_with_values():
    ctx = PropagationContext(
        trace_id="1234567890abcdef1234567890abcdef",
        span_id="1234567890abcdef",
        parent_span_id="abcdef1234567890",
        parent_sampled=True,
        dynamic_sampling_context={
            "foo": "bar",
        },
    )

    assert ctx.trace_id == "1234567890abcdef1234567890abcdef"
    assert ctx.span_id == "1234567890abcdef"
    assert ctx.parent_span_id == "abcdef1234567890"
    assert ctx.parent_sampled
    assert ctx.dynamic_sampling_context == {
        "foo": "bar",
    }


def test_lazy_uuids():
    ctx = PropagationContext()
    assert ctx._trace_id is None
    assert ctx._span_id is None

    assert ctx.trace_id is not None  # this sets _trace_id
    assert ctx._trace_id is not None
    assert ctx._span_id is None

    assert ctx.span_id is not None  # this sets _span_id
    assert ctx._trace_id is not None
    assert ctx._span_id is not None


def test_property_setters():
    ctx = PropagationContext()

    ctx.trace_id = "X234567890abcdef1234567890abcdef"
    ctx.span_id = "X234567890abcdef"

    assert ctx._trace_id == "X234567890abcdef1234567890abcdef"
    assert ctx.trace_id == "X234567890abcdef1234567890abcdef"
    assert ctx._span_id == "X234567890abcdef"
    assert ctx.span_id == "X234567890abcdef"
    assert ctx.dynamic_sampling_context is None


def test_update():
    ctx = PropagationContext()

    other_data = {
        "trace_id": "Z234567890abcdef1234567890abcdef",
        "parent_span_id": "Z234567890abcdef",
        "parent_sampled": False,
        "foo": "bar",
    }
    ctx.update(other_data)

    assert ctx._trace_id == "Z234567890abcdef1234567890abcdef"
    assert ctx.trace_id == "Z234567890abcdef1234567890abcdef"
    assert ctx._span_id is None  # this will be set lazily
    assert ctx.span_id is not None  # this sets _span_id
    assert ctx._span_id is not None
    assert ctx.parent_span_id == "Z234567890abcdef"
    assert not ctx.parent_sampled
    assert ctx.dynamic_sampling_context is None

    assert not hasattr(ctx, "foo")


def test_existing_sample_rand_kept():
    ctx = PropagationContext(
        trace_id="00000000000000000000000000000000",
        dynamic_sampling_context={"sample_rand": "0.5"},
    )

    # If sample_rand was regenerated, the value would be 0.919221 based on the trace_id
    assert ctx.dynamic_sampling_context["sample_rand"] == "0.5"


@pytest.mark.parametrize(
    ("parent_sampled", "sample_rate", "expected_interval"),
    (
        # Note that parent_sampled and sample_rate do not scale the
        # sample_rand value, only determine the range of the value.
        # Expected values are determined by parent_sampled, sample_rate,
        # and the trace_id.
        (None, None, (0.0, 1.0)),
        (None, "0.5", (0.0, 1.0)),
        (False, None, (0.0, 1.0)),
        (True, None, (0.0, 1.0)),
        (False, "0.0", (0.0, 1.0)),
        (False, "0.01", (0.01, 1.0)),
        (True, "0.01", (0.0, 0.01)),
        (False, "0.1", (0.1, 1.0)),
        (True, "0.1", (0.0, 0.1)),
        (False, "0.5", (0.5, 1.0)),
        (True, "0.5", (0.0, 0.5)),
        (True, "1.0", (0.0, 1.0)),
    ),
)
def test_sample_rand_filled(parent_sampled, sample_rate, expected_interval):
    """When continuing a trace, we want to fill in the sample_rand value if it's missing."""
    if sample_rate is not None:
        sample_rate_str = f",sentry-sample_rate={sample_rate}"  # noqa: E231
    else:
        sample_rate_str = ""

    # for convenience, we'll just return the lower bound of the interval as an integer
    mock_randrange = mock.Mock(return_value=int(expected_interval[0] * 1000000))

    def mock_random_class(seed):
        assert seed == "00000000000000000000000000000000", "seed should be the trace_id"
        rv = Mock()
        rv.randrange = mock_randrange
        return rv

    with mock.patch("sentry_sdk.tracing_utils.Random", mock_random_class):
        ctx = PropagationContext().from_incoming_data(
            {
                "sentry-trace": f"00000000000000000000000000000000-0000000000000000{SAMPLED_FLAG[parent_sampled]}",
                # Placeholder is needed, since we only add sample_rand if sentry items are present in baggage
                "baggage": f"sentry-placeholder=asdf{sample_rate_str}",
            }
        )

    assert (
        ctx.dynamic_sampling_context["sample_rand"] == f"{expected_interval[0]:.6f}"  # noqa: E231
    )
    assert mock_randrange.call_count == 1
    assert mock_randrange.call_args[0] == (
        int(expected_interval[0] * 1000000),
        int(expected_interval[1] * 1000000),
    )


def test_sample_rand_rounds_down():
    # Mock value that should round down to 0.999_999
    mock_randrange = mock.Mock(return_value=999999)

    def mock_random_class(_):
        rv = Mock()
        rv.randrange = mock_randrange
        return rv

    with mock.patch("sentry_sdk.tracing_utils.Random", mock_random_class):
        ctx = PropagationContext().from_incoming_data(
            {
                "sentry-trace": "00000000000000000000000000000000-0000000000000000",
                "baggage": "sentry-placeholder=asdf",
            }
        )

    assert ctx.dynamic_sampling_context["sample_rand"] == "0.999999"
